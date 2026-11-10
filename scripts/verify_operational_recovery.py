"""Interrupt project worker/retrieval only, always restore services, never call an LLM."""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
import psutil

from backend.app.core.config import Settings
from scripts.local_services import ROOT, SERVICES, STATE, owned


def pause(name):
    state = json.loads(STATE.read_text())
    process = psutil.Process(state[name])
    executable, arguments, _ = SERVICES[name]
    if not owned(process, executable, arguments):
        raise RuntimeError("Refusing to interrupt an unrelated process")
    process.terminate()
    process.wait(timeout=15)


def main():
    settings = Settings(_env_file=ROOT / ".env")
    private = json.loads((ROOT / ".mysql-local/credentials.json").read_text())
    report = {
        "at": datetime.now(UTC).isoformat(),
        "scope": "PROJECT_WORKER_AND_RETRIEVAL_ONLY; NO_DATABASE_OR_PROVIDER_OUTAGE",
        "checks": {},
        "samples": [],
    }
    checks = report["checks"]
    with httpx.Client(base_url="http://127.0.0.1:8010", trust_env=False, timeout=6) as client:
        login = client.post(
            "/api/v1/auth/login", json={"login": "admin", "password": private["admin_password"]}
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        def snapshot(phase):
            response = client.get("/api/v1/admin/operations/health")
            response.raise_for_status()
            data = response.json()
            report["samples"].append({"phase": phase, **data})
            return {row["name"]: row["status"] for row in data["components"]}, data

        statuses, before = snapshot("baseline")
        checks["baseline_components_healthy"] = all(
            value == "healthy" for value in statuses.values()
        )
        try:
            if not checks["baseline_components_healthy"]:
                raise RuntimeError("Fix baseline component failures before interrupting services")
            pause("worker")
            started, deadline = (
                time.monotonic(),
                time.monotonic() + settings.worker_stale_seconds + 40,
            )
            while True:
                statuses, _ = snapshot("worker_interrupted")
                if statuses["worker"] != "healthy":
                    checks["worker_stop_detected_without_editing_heartbeat"] = True
                    report["worker_detection_seconds"] = time.monotonic() - started
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Worker interruption was not detected within configured heartbeat window"
                    )
                time.sleep(5)
            checks["api_live_during_worker_interruption"] = (
                client.get("/health/live").status_code == 200
            )
            pause("retrieval")
            statuses, _ = snapshot("retrieval_interrupted")
            checks["retrieval_stop_detected"] = statuses["retrieval"] == "degraded"
            checks["database_unchanged_availability"] = statuses["database"] == "healthy"
        finally:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "scripts.local_services", "start"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=150,
            )
            checks["services_restart_succeeded"] = result.returncode == 0
        deadline = time.monotonic() + 45
        while True:
            statuses, after = snapshot("restored")
            if (
                all(value == "healthy" for value in statuses.values())
                or time.monotonic() > deadline
            ):
                break
            time.sleep(2)
        checks["components_recovered"] = all(value == "healthy" for value in statuses.values())
        checks["paid_call_count_unchanged"] = (
            before["llm_budget"]["reserved"] == after["llm_budget"]["reserved"]
        )
    report["passed"] = all(checks.values())
    target = ROOT / "artifacts/validation/operational-recovery.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print({"report": str(target), "passed": report["passed"], "checks": checks})
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
