"""Single-instance poller; durable leases + idempotent SLA scan survive restart."""

import argparse
import time
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.core.observability import WorkerHeartbeat
from backend.app.db.runtime import Database
from backend.app.services.index_jobs import process_one as process_index
from backend.app.services.jobs import process_one
from backend.app.services.operations import scan_sla


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env")
    database = Database(settings)
    heartbeat = WorkerHeartbeat(settings.worker_heartbeat_path)
    try:
        heartbeat.write("starting")
        while True:
            heartbeat.write("running")
            try:
                for _ in range(100):
                    if not process_one(database, settings):
                        break
                process_index(database, settings)
                with database.sessions() as session:
                    print(scan_sla(session), flush=True)
                heartbeat.write("ok")
            except Exception as exc:
                heartbeat.write("error", exc)
                print({"worker_error": type(exc).__name__, "retry_in_seconds": 30}, flush=True)
                if args.once:
                    raise
            if args.once:
                break
            time.sleep(30)
    finally:
        try:
            heartbeat.write("stopped")
        finally:
            database.dispose()


if __name__ == "__main__":
    main()
