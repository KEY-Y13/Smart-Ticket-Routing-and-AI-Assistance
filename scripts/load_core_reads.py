"""Bounded local read-only load check. Never calls assistant/search or changes business data."""

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PATHS = ("/health/ready", "/api/v1/tickets?page_size=20", "/api/v1/metrics")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.requests <= 2000 or not 1 <= args.concurrency <= 32:
        raise ValueError("Use a bounded local workload")
    credentials = json.loads((ROOT / ".mysql-local/credentials.json").read_text())
    with httpx.Client(base_url="http://127.0.0.1:8010", trust_env=False, timeout=15) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "login": "admin",
                "password": credentials["admin_password"],
            },
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        before = client.get("/api/v1/metrics").json()["llm_invocations"]

        def request(index):
            path = PATHS[index % len(PATHS)]
            start = time.perf_counter()
            try:
                response = client.get(path)
                status = str(response.status_code)
            except httpx.HTTPError as exc:
                status = type(exc).__name__
            return {"path": path, "status": status, "ms": (time.perf_counter() - start) * 1000}

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            rows = list(executor.map(request, range(args.requests)))
        seconds = time.perf_counter() - started
        after = client.get("/api/v1/metrics").json()["llm_invocations"]
    latencies = [row["ms"] for row in rows]
    p95 = float(np.percentile(latencies, 95))
    report = {
        "at": datetime.now(UTC).isoformat(),
        "scope": "LOCAL_CORE_READS_ONLY; NOT_SUSTAINED_PRODUCTION_OR_LLM_CAPACITY",
        "predeclared_target": "400 reads/8 concurrent by default; all 200, P95 <2000ms",
        "requests": len(rows),
        "concurrency": args.concurrency,
        "elapsed_seconds": seconds,
        "requests_per_second": len(rows) / seconds,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "p50_ms": float(np.percentile(latencies, 50)),
        "p95_ms": p95,
        "max_ms": max(latencies),
        "llm_invocations_unchanged": before == after,
        "passed": all(row["status"] == "200" for row in rows) and p95 < 2000 and before == after,
        "results": rows,
    }
    target = ROOT / "artifacts/validation/core-read-load.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({key: value for key, value in report.items() if key != "results"})
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
