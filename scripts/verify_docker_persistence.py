"""Restart only this project's services; compare persisted data without exposing row values."""

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SNAPSHOT = r"""
import hashlib, json
from sqlalchemy import inspect, text
from backend.app.core.config import Settings
from backend.app.db.runtime import Database
db = Database(Settings())
result = {"tables": {}}
with db.engine.connect() as con:
    schema = con.execute(text("SELECT DATABASE()")).scalar_one()
    assert schema == "smartticket_ai"
    inspector = inspect(con)
    for table in sorted(inspector.get_table_names()):
        primary = inspector.get_pk_constraint(table)["constrained_columns"]
        assert primary
        order = ",".join("`" + key + "`" for key in primary)
        rows = [tuple(row) for row in con.exec_driver_sql(
            f"SELECT * FROM `{table}` ORDER BY {order}").all()]
        encoded = json.dumps(rows, default=str, ensure_ascii=False, separators=(",", ":"))
        result["tables"][table] = {
            "count": len(rows), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
    grants = con.execute(text(
        "SELECT TABLE_NAME, PRIVILEGE_TYPE FROM information_schema.TABLE_PRIVILEGES "
        "WHERE TABLE_SCHEMA=:schema ORDER BY TABLE_NAME,PRIVILEGE_TYPE"),
        {"schema": schema}).all()
    result["table_privileges"] = [list(row) for row in grants]
    result["schema_privileges"] = [list(row) for row in con.execute(text(
        "SELECT PRIVILEGE_TYPE FROM information_schema.SCHEMA_PRIVILEGES "
        "WHERE TABLE_SCHEMA=:schema"), {"schema": schema}).all()]
    result["global_privileges"] = [row[0] for row in con.execute(text(
        "SELECT PRIVILEGE_TYPE FROM information_schema.USER_PRIVILEGES")).all()]
print(json.dumps(result))
db.dispose()
"""
INDEX_SNAPSHOT = r"""
import hashlib, json
from pathlib import Path
root = Path("/app/artifacts/indexes")
print(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(root.rglob("*")) if p.is_file()}))
"""
GOVERNANCE_PROBE = r"""
import json
from pathlib import Path
p = Path('/app/artifacts/governance/validation/volume-probe.json')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({'purpose': 'ISOLATED_STORAGE_PROBE_NOT_MODEL_APPROVAL'}))
print('Non-root API wrote governance storage probe')
"""
WORKER_PROBE = r"""
import errno
from pathlib import Path
p = Path('/app/artifacts/governance/validation/volume-probe.json')
assert 'NOT_MODEL_APPROVAL' in p.read_text()
try:
    p.write_text('worker-must-not-write')
except OSError as exc:
    assert exc.errno == errno.EROFS
    print('readonly_verified')
else:
    raise RuntimeError('Worker governance mount is writable')
"""
OPERATIONS_WORKER_PROBE = r"""
from pathlib import Path
p = Path('/app/artifacts/operations/validation-probe.json')
p.write_text('WORKER_OPERATIONAL_VOLUME_PROBE')
print('worker_operations_write_verified')
"""
OPERATIONS_API_PROBE = r"""
import errno
from pathlib import Path
p = Path('/app/artifacts/operations/validation-probe.json')
assert p.read_text() == 'WORKER_OPERATIONAL_VOLUME_PROBE'
try:
    p.write_text('api-must-not-write')
except OSError as exc:
    assert exc.errno == errno.EROFS
    print('api_operations_readonly_verified')
else:
    raise RuntimeError('API operational mount is writable')
"""


def compose(*args, source=None):
    result = subprocess.run(
        ["docker", "compose", "--profile", "rag", *args],
        input=source,
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=ROOT,
        timeout=150,
    )
    if result.returncode:
        # Do not echo provider errors, which may contain credentials or row values.
        raise RuntimeError("Project Compose operation failed: " + args[0])
    return result.stdout


def snapshot():
    return {
        "database": json.loads(
            compose("exec", "-T", "api", "python", "-", source=DATABASE_SNAPSHOT)
        ),
        "indexes": json.loads(
            compose("exec", "-T", "retrieval", "python", "-", source=INDEX_SNAPSHOT)
        ),
        "governance": json.loads(
            compose(
                "exec",
                "-T",
                "api",
                "python",
                "-",
                source=INDEX_SNAPSHOT.replace(
                    "/app/artifacts/indexes", "/app/artifacts/governance"
                ),
            )
        ),
    }


def main():
    compose("exec", "-T", "api", "python", "-", source=GOVERNANCE_PROBE)
    worker_readonly = "readonly_verified" in compose(
        "exec",
        "-T",
        "worker",
        "python",
        "-",
        source=WORKER_PROBE,
    )
    worker_operations_write = "worker_operations_write_verified" in compose(
        "exec",
        "-T",
        "worker",
        "python",
        "-",
        source=OPERATIONS_WORKER_PROBE,
    )
    api_operations_readonly = "api_operations_readonly_verified" in compose(
        "exec",
        "-T",
        "api",
        "python",
        "-",
        source=OPERATIONS_API_PROBE,
    )
    compose("stop", "worker")
    report = {"at": datetime.now(UTC).isoformat(), "scope": "smartticket-ai only"}
    try:
        before = snapshot()
        compose("restart", "db", "api", "retrieval")
        with httpx.Client(trust_env=False, timeout=3) as client:
            deadline = time.monotonic() + 90
            while True:
                try:
                    if client.get("http://127.0.0.1:8012/health/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Project API did not recover within 90 seconds")
                time.sleep(2)
        after = snapshot()
        grants = after["database"]["table_privileges"]
        readonly = {
            "orders",
            "products",
            "order_items",
            "payments",
            "shipments",
            "refunds",
            "alembic_version",
        }
        append = {
            "ticket_events",
            "admin_events",
            "knowledge_chunks",
            "ai_feedback",
            "model_analyses",
            "review_collections",
            "review_decisions",
            "review_snapshots",
        }
        privileges = {}
        for table, privilege in grants:
            privileges.setdefault(table, set()).add(privilege)
        expected = {
            table: (
                {"SELECT"}
                if table in readonly
                else {"SELECT", "INSERT"}
                if table in append
                else {"SELECT", "INSERT", "UPDATE"}
            )
            for table in after["database"]["tables"]
        }
        least_privilege = (
            privileges == expected
            and not after["database"]["schema_privileges"]
            and set(after["database"]["global_privileges"]) <= {"USAGE"}
        )
        report.update(
            before=before,
            after=after,
            all_table_counts_and_hashes_match=before["database"] == after["database"],
            index_files_unchanged=bool(before["indexes"]) and before["indexes"] == after["indexes"],
            least_privilege=least_privilege,
            worker_governance_readonly=worker_readonly,
            worker_operations_write=worker_operations_write,
            api_operations_readonly=api_operations_readonly,
            governance_files_unchanged=bool(before["governance"])
            and before["governance"] == after["governance"],
        )
        report["passed"] = all(
            report[key]
            for key in (
                "all_table_counts_and_hashes_match",
                "index_files_unchanged",
                "least_privilege",
                "worker_governance_readonly",
                "worker_operations_write",
                "api_operations_readonly",
                "governance_files_unchanged",
            )
        )
    finally:
        compose("start", "worker")
    target = ROOT / "artifacts/validation/docker-restart-persistence.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({"report": str(target), "passed": report["passed"]})
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
