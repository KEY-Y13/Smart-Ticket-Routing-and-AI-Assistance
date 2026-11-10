"""One-command local demo lifecycle, with executable/module verification before termination."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from backend.app.core.config import Settings
from scripts.local_mysql import start as start_mysql

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".mysql-local/services.json"
SERVICES = {
    "api": (
        ROOT / ".conda/python.exe",
        [
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8010",
            "--env-file",
            str(ROOT / ".env"),
            "--no-access-log",
        ],
        8010,
    ),
    "retrieval": (
        ROOT / ".conda-bert/python.exe",
        [
            "-m",
            "uvicorn",
            "rag.worker:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8011",
            "--env-file",
            str(ROOT / ".env"),
            "--no-access-log",
        ],
        8011,
    ),
    "worker": (ROOT / ".conda/python.exe", ["-m", "scripts.run_worker"], None),
}


def owned(process, executable, args):
    try:
        return Path(process.exe()).resolve() == executable.resolve() and all(
            part in process.cmdline() for part in args[:3]
        )
    except (psutil.Error, OSError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "restart"])
    action = parser.parse_args().action
    if action == "status":
        records = json.loads(STATE.read_text()) if STATE.exists() else {}
        print(
            {
                name: bool(pid and psutil.pid_exists(pid) and owned(psutil.Process(pid), exe, args))
                for name, (exe, args, port) in SERVICES.items()
                for pid in [records.get(name)]
            }
        )
        return
    if action in {"stop", "restart"}:
        records = json.loads(STATE.read_text()) if STATE.exists() else {}
        for name in ("worker", "api", "retrieval"):
            pid = records.get(name)
            exe, args, _ = SERVICES[name]
            if pid and psutil.pid_exists(pid):
                process = psutil.Process(pid)
                if not owned(process, exe, args):
                    raise RuntimeError("Refusing to terminate a process outside this project")
                process.terminate()
                process.wait(timeout=15)
        STATE.write_text("{}", encoding="utf-8")
        if action == "stop":
            print("Project API/model/worker stopped; project MySQL remains running.")
            return
    start_mysql()
    if not (ROOT / "frontend/dist/index.html").exists():
        subprocess.run(
            ["npm.cmd" if os.name == "nt" else "npm", "run", "build"],
            cwd=ROOT / "frontend",
            check=True,
        )
    records = {}
    logdir = ROOT / "artifacts/services"
    logdir.mkdir(parents=True, exist_ok=True)
    for name, (executable, args, port) in SERVICES.items():
        matches = [p for p in psutil.process_iter() if owned(p, executable, args)]
        if matches:
            records[name] = matches[0].pid
            continue
        if port and any(
            c.laddr.port == port and c.status == psutil.CONN_LISTEN
            for c in psutil.net_connections(kind="tcp")
        ):
            raise RuntimeError(f"Port {port} belongs to another process; refusing to replace it")
        with (logdir / f"{name}.log").open("a", encoding="utf-8") as log:
            kwargs = (
                {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            process = subprocess.Popen(
                [str(executable), "-X", "utf8", *args], cwd=ROOT, stdout=log, stderr=log, **kwargs
            )
        records[name] = process.pid
        STATE.write_text(json.dumps(records), encoding="utf-8")
    STATE.write_text(json.dumps(records), encoding="utf-8")
    settings = Settings(_env_file=ROOT / ".env")
    if not settings.retrieval_secret:
        raise RuntimeError("Local model service requires a private retrieval token")
    headers = {"Authorization": "Bearer " + settings.retrieval_secret.get_secret_value()}
    with httpx.Client(trust_env=False, timeout=3) as client:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                api_ready = client.get("http://127.0.0.1:8010/health/ready").status_code == 200
                model_health = client.get("http://127.0.0.1:8011/health", headers=headers)
                model_ready = model_health.status_code == 200 and model_health.json().get("alive")
                if api_ready and model_ready:
                    print(
                        "SmartTicket API and model service ready: http://127.0.0.1:8010\n"
                        "Accounts: .mysql-local/login-info.txt (private)"
                    )
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise RuntimeError("Startup did not become ready; inspect artifacts/services logs")


if __name__ == "__main__":
    main()
