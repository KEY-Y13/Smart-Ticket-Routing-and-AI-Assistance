"""Restore a project DB dump into a NEW named volume; preserve source and drill resources."""

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.verify_docker_persistence import DATABASE_SNAPSHOT, compose

ROOT = Path(__file__).resolve().parents[1]


def run(command, *, data=None, env=None, timeout=150):
    result = subprocess.run(
        command, input=data, capture_output=True, cwd=ROOT, env=env, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(
            f"Docker restore operation failed (exit {result.returncode}): " + " ".join(command[:4])
        )
    return result.stdout


def db_client(container, program, arguments=(), *, data=None):
    if program not in {"mysql", "mysqldump"}:
        raise ValueError("Unsupported database client")
    # A private, transient client option file avoids secrets in argv, env and logs.
    # Its exact path is inside the verified project container, and is always removed.
    script = (
        "set -eu; umask 077; cnf=$(mktemp /tmp/smartticket-client.XXXXXX); "
        "trap 'rm -f -- \"$cnf\"' EXIT; "
        "password=$(cat /run/secrets/root_password); "
        "printf '[client]\\nuser=root\\npassword=%s\\nprotocol=socket\\n' "
        '"$password" >"$cnf"; unset password; '
        f'exec_program={program}; "$exec_program" --defaults-extra-file="$cnf" "$@"'
    )
    return run(
        ["docker", "exec", "-i", container, "sh", "-c", script, "client", *arguments], data=data
    )


def main():
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    if not re.fullmatch(r"\d{20}", stamp):
        raise ValueError("Unexpected drill identifier")
    source_id = compose("ps", "-q", "db").strip()
    source_label = (
        run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}|'
                "{{range .Mounts}}{{.Name}}{{end}}",
                source_id,
            ]
        )
        .decode()
        .strip()
    )
    if source_label != "smartticket-ai|smartticket-ai_mysql_data":
        raise ValueError("Refusing a source outside the exact project volume")
    private = ROOT / ".mysql-local/backups" / ("docker-" + stamp)
    private.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "SMARTTICKET_RESTORE_ID": stamp}
    restore_cmd = ["docker", "compose", "-f", "compose.restore.yaml"]
    run([*restore_cmd, "config", "--quiet"], env=env)
    compose("stop", "worker")
    report = {"at": datetime.now(UTC).isoformat(), "source_volume": "smartticket-ai_mysql_data"}
    try:
        before = json.loads(compose("exec", "-T", "api", "python", "-", source=DATABASE_SNAPSHOT))
        backup = private / "smartticket_ai.sql"
        backup.write_bytes(
            db_client(
                source_id,
                "mysqldump",
                (
                    "--single-transaction",
                    "--no-tablespaces",
                    "--set-gtid-purged=OFF",
                    "--skip-add-drop-table",
                    "--skip-add-locks",
                    "smartticket_ai",
                ),
            )
        )
        run([*restore_cmd, "up", "-d"], env=env)
        target_id = run([*restore_cmd, "ps", "-q", "restore-db"], env=env).decode().strip()
        target_volume = "smartticket-ai_restore_" + stamp
        target_label = (
            run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.project"}}|'
                    "{{range .Mounts}}{{.Name}}{{end}}",
                    target_id,
                ]
            )
            .decode()
            .strip()
        )
        if target_label != "smartticket-ai-restore-" + stamp + "|" + target_volume:
            raise ValueError("Refusing a target outside the new drill volume")
        deadline = time.monotonic() + 150
        while True:
            health = run(["docker", "inspect", "--format", "{{.State.Health.Status}}", target_id])
            if health.strip() == b"healthy":
                try:
                    schema_ready = (
                        db_client(
                            target_id,
                            "mysql",
                            (
                                "--batch",
                                "--skip-column-names",
                                "-e",
                                "SELECT COUNT(*) FROM information_schema.SCHEMATA "
                                "WHERE SCHEMA_NAME='smartticket_ai'",
                            ),
                        ).strip()
                        == b"1"
                    )
                    if schema_ready:
                        break
                except RuntimeError:
                    pass  # mysqladmin ping can precede authenticated bootstrap completion.
            if time.monotonic() > deadline:
                raise RuntimeError("New recovery database did not become healthy")
            time.sleep(3)
        count = db_client(
            target_id,
            "mysql",
            (
                "--batch",
                "--skip-column-names",
                "-e",
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA='smartticket_ai'",
            ),
        ).strip()
        if count != b"0":
            raise ValueError("Refusing to import into any nonempty target schema")
        db_client(
            target_id, "mysql", ("--binary-mode=1", "smartticket_ai"), data=backup.read_bytes()
        )
        # A one-shot Python checker has root credentials mounted by migrate, never in argv.
        script = DATABASE_SNAPSHOT.replace(
            "db = Database(Settings())",
            "from sqlalchemy.engine import make_url\n"
            "settings = Settings()\n"
            "url = make_url(settings.database_url.get_secret_value())\n"
            f"url = url.set(host='smartticket-restore-{stamp}')\n"
            "settings.database_url = __import__('pydantic').SecretStr("
            "url.render_as_string(hide_password=False))\n"
            "db = Database(settings)",
        )
        restored = json.loads(
            run(
                [
                    "docker",
                    "compose",
                    "run",
                    "-T",
                    "--no-deps",
                    "--name",
                    "smartticket-ai-restore-check-" + stamp,
                    "--entrypoint",
                    "python",
                    "migrate",
                    "-",
                ],
                data=script.encode(),
            ).decode()
        )
        after = json.loads(compose("exec", "-T", "api", "python", "-", source=DATABASE_SNAPSHOT))
        report.update(
            target_volume=target_volume,
            target_container=target_id,
            source_unchanged=before["tables"] == after["tables"],
            all_table_counts_and_hashes_match=before["tables"] == restored["tables"],
            tables=restored["tables"],
            backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
            backup_private_path=str(backup),
            source_volume_deleted=False,
            overwritten_targets=[],
            recovered_volume_preserved=True,
        )
        report["passed"] = (
            report["source_unchanged"] and report["all_table_counts_and_hashes_match"]
        )
    finally:
        try:
            run([*restore_cmd, "stop"], env=env)
        finally:
            compose("start", "worker")
    path = ROOT / "artifacts/validation/docker-volume-restore.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({"report": str(path), "passed": report["passed"], "new_volume": report["target_volume"]})
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
