"""Bounded sustained local reads, with predeclared thresholds; never invokes paid/search APIs."""

import argparse
import json
import math
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import psutil

from scripts.provision_local_mysql import ROOT

PATHS = ("/health/ready", "/api/v1/tickets?page_size=20", "/api/v1/metrics")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--rps", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if not 30 <= args.seconds <= 600 or not 1 <= args.rps <= 10 or not 1 <= args.concurrency <= 8:
        raise ValueError("Only bounded local workloads are permitted")
    private = json.loads((ROOT / ".mysql-local/credentials.json").read_text())
    lock = threading.Lock()
    rows, resources = [], []
    with httpx.Client(base_url="http://127.0.0.1:8010", trust_env=False, timeout=10) as client:
        login = client.post(
            "/api/v1/auth/login", json={"login": "admin", "password": private["admin_password"]}
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        before = client.get("/api/v1/metrics").json()["llm_invocations"]
        started, count = time.monotonic(), 0

        def run():
            nonlocal count
            while True:
                with lock:
                    index = count
                    if index >= args.seconds * args.rps:
                        return
                    count += 1
                target = started + index / args.rps
                remaining = target - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                start = time.monotonic()
                try:
                    status = str(client.get(PATHS[index % len(PATHS)]).status_code)
                except httpx.HTTPError as exc:
                    status = type(exc).__name__
                with lock:
                    rows.append(
                        {
                            "path": PATHS[index % len(PATHS)],
                            "status": status,
                            "elapsed_ms": (time.monotonic() - start) * 1000,
                            "schedule_lag_ms": max(0, start - target) * 1000,
                        }
                    )
                    if index % (args.rps * 10) == 0:
                        resources.append(
                            {
                                "seconds": time.monotonic() - started,
                                "memory_percent": psutil.virtual_memory().percent,
                            }
                        )

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            list(executor.map(lambda _: run(), range(args.concurrency)))
        elapsed = time.monotonic() - started
        after = client.get("/api/v1/metrics").json()["llm_invocations"]
    times = sorted(row["elapsed_ms"] for row in rows)
    p95 = times[math.ceil(len(times) * 0.95) - 1]
    achieved_rps = len(rows) / elapsed
    report = {
        "at": datetime.now(UTC).isoformat(),
        "scope": "SUSTAINED_LOCAL_CORE_READS; NOT_PUBLIC_PRODUCTION_OR_LLM_CAPACITY",
        "predeclared_target": {
            "seconds": args.seconds,
            "scheduled_rps": args.rps,
            "max_inflight": args.concurrency,
            "all_http_200": True,
            "p95_ms_below": 1500,
            "achieved_rate_fraction_at_least": 0.9,
        },
        "requests": len(rows),
        "elapsed_seconds": elapsed,
        "achieved_rps": achieved_rps,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "p95_ms": p95,
        "max_schedule_lag_ms": max(row["schedule_lag_ms"] for row in rows),
        "paid_invocations_unchanged": before == after,
        "resources": resources,
        "passed": all(row["status"] == "200" for row in rows)
        and p95 < 1500
        and achieved_rps >= args.rps * 0.9
        and before == after,
        "results": rows,
    }
    target = ROOT / "artifacts/validation/sustained-local-load.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({key: value for key, value in report.items() if key not in {"results", "resources"}})
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
