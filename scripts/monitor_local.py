"""Read local authenticated health; emit local transition alerts only, no external messages."""

import argparse
import json
import time
from datetime import UTC, datetime

import httpx

from scripts.provision_local_mysql import ROOT


def event_from_snapshot(snapshot, previous):
    status = snapshot["status"]
    fingerprint = json.dumps(
        {"status": status, "alerts": sorted(snapshot["alerts"])}, sort_keys=True
    )
    if fingerprint == previous:
        return None, fingerprint
    return {
        "at": datetime.now(UTC).isoformat(),
        "event": "RECOVERED" if status == "healthy" else "ATTENTION",
        "alerts": snapshot["alerts"],
    }, fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch-seconds", type=int, default=0)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if not 0 <= args.watch_seconds <= 3600 or not 5 <= args.interval <= 60:
        raise ValueError("Use bounded local monitoring")
    credentials = json.loads((ROOT / ".mysql-local/credentials.json").read_text())
    output = ROOT / "artifacts/services/operations-alerts.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    previous, started, last_status = None, time.monotonic(), "unavailable"
    with httpx.Client(base_url="http://127.0.0.1:8010", trust_env=False, timeout=5) as client:
        response = client.post(
            "/api/v1/auth/login", json={"login": "admin", "password": credentials["admin_password"]}
        )
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        while True:
            try:
                response = client.get("/api/v1/admin/operations/health")
                response.raise_for_status()
                snapshot = response.json()
            except (httpx.HTTPError, ValueError):
                snapshot = {
                    "status": "degraded",
                    "alerts": ["API不可用或登录已过期，请检查服务并重新运行监控"],
                }
            event, previous = event_from_snapshot(snapshot, previous)
            last_status = snapshot["status"]
            if event:
                with output.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                print(json.dumps(event, ensure_ascii=False), flush=True)
            if time.monotonic() - started >= args.watch_seconds:
                break
            time.sleep(
                max(0, min(args.interval, args.watch_seconds - (time.monotonic() - started)))
            )
    return 0 if last_status == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
