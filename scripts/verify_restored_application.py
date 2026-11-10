"""Recreate least-privilege access and verify an application against the recovered volume."""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(report):
    from scripts.verify_docker_persistence import DATABASE_SNAPSHOT, compose
    from scripts.verify_docker_restore import db_client, run

    volume, target = report["target_volume"], report["target_container"]
    if not re.fullmatch(r"smartticket-ai_restore_\d{20}", volume):
        raise ValueError("Refusing a target outside a named recovery drill")
    stamp = volume.removeprefix("smartticket-ai_restore_")
    label = (
        run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}|'
                "{{range .Mounts}}{{.Name}}{{end}}",
                target,
            ]
        )
        .decode()
        .strip()
    )
    if label != f"smartticket-ai-restore-{stamp}|{volume}":
        raise ValueError("Recovery target identity mismatch")
    running = (
        run(["docker", "inspect", "--format", "{{.State.Running}}", target]).strip() == b"true"
    )
    source = json.loads(compose("exec", "-T", "api", "python", "-", source=DATABASE_SNAPSHOT))
    grants = {}
    for table, right in source["table_privileges"]:
        if not re.fullmatch(r"[a-z_]+", table) or right not in {"SELECT", "INSERT", "UPDATE"}:
            raise ValueError("Unexpected source application privilege")
        grants.setdefault(table, []).append(right)
    if set(grants) != set(report["tables"]):
        raise ValueError("Incomplete source privileges")
    try:
        if not running:
            run(["docker", "start", target])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if db_client(target, "mysql", ("--batch", "-e", "SELECT 1")).strip():
                    break
            except RuntimeError:
                time.sleep(2)
        else:
            raise RuntimeError("Recovery target did not become ready")
        setup = f"""
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from pydantic import SecretStr
from pathlib import Path
settings = Settings()
url = make_url(settings.database_url.get_secret_value())
url = url.set(host='smartticket-restore-{stamp}')
password = Path('/run/secrets/app_password').read_text().strip()
engine = create_engine(url, hide_parameters=True)
with engine.begin() as con:
    sql = "SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host=%s"
    if not con.exec_driver_sql(sql, ('smartticket_app', '%')).scalar():
        con.exec_driver_sql("CREATE USER %s@%s IDENTIFIED BY %s",
                             ('smartticket_app', '%', password))
    for table, rights in {grants!r}.items():
        sql = "GRANT " + ','.join(rights) + " ON smartticket_ai.`" + table + "` TO %s@%s"
        con.exec_driver_sql(sql, ('smartticket_app', '%'))
engine.dispose()
url = url.set(username='smartticket_app', password=password)
settings.database_url = SecretStr(url.render_as_string(hide_password=False))
db = Database(settings)
"""
        check = """
import asyncio, httpx
from backend.app.main import create_app
async def verify_application():
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url='http://restore') as client:
        ready = await client.get('/health/ready')
        password = Path('/run/secrets/admin_password').read_text().strip()
        login = await client.post('/api/v1/auth/login',
                                  json={'login':'admin','password':password})
        assert ready.status_code == 200 and login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
        tickets = await client.get('/api/v1/tickets')
        assert tickets.status_code == 200
        assert tickets.json()['total'] == result['tables']['tickets']['count']
        result['application_ready'] = True
        result['restored_admin_login'] = True
        result['restored_ticket_read'] = True
asyncio.run(verify_application())
print(json.dumps(result))
"""
        script = DATABASE_SNAPSHOT.replace("db = Database(Settings())", setup).replace(
            "print(json.dumps(result))",
            check,
        )
        data = json.loads(
            run(
                [
                    "docker",
                    "compose",
                    "run",
                    "-T",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "migrate",
                    "-",
                ],
                data=script.encode(),
            ).decode()
        )
        return {
            "application_ready": data["application_ready"],
            "restored_admin_login": data["restored_admin_login"],
            "restored_ticket_read": data["restored_ticket_read"],
            "least_privilege_restored": data["table_privileges"] == source["table_privileges"],
            "restored_data_still_matches": data["tables"] == report["tables"],
        }
    finally:
        if not running:
            run(["docker", "stop", "--time", "15", target])


def main():
    path = ROOT / "artifacts/validation/docker-volume-restore.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["application"] = verify(report)
    report["passed"] = report["passed"] and all(report["application"].values())
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({"passed": report["passed"], "application": report["application"]})
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
