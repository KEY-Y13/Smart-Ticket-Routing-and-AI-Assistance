"""Complete only an interrupted, system-tables-only project MySQL initialization."""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mysql(sql):
    return subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "mysql",
            "--protocol=socket",
            "-uroot",
            "--skip-password",
            "--batch",
            "--skip-column-names",
        ],
        input=sql.encode(),
        capture_output=True,
        cwd=ROOT,
        timeout=20,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    apply = parser.parse_args().apply
    label = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.project"}}|'
            "{{range .Mounts}}{{.Name}}{{end}}",
            "smartticket-ai-db-1",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()
    if label != "smartticket-ai|smartticket-ai_mysql_data":
        raise RuntimeError("Refusing a container/volume outside this project")
    probe = mysql(
        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN "
        "('mysql','sys','information_schema','performance_schema'); "
        "SELECT CONCAT(User,'@',Host) FROM mysql.user WHERE User='root';"
    )
    if probe.returncode or probe.stdout.decode().strip() != "root@localhost":
        raise RuntimeError(
            "Not the recognized empty partial initialization; refusing account changes"
        )
    password = (ROOT / ".docker-secrets/root_password").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", password):
        raise RuntimeError("Unexpected secret format")
    if not apply:
        print(
            "Verified project-only partial initialization. --apply completes password, "
            "network root and empty named database; no deletion."
        )
        return
    sql = (
        f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{password}'; "
        f"CREATE USER 'root'@'%' IDENTIFIED BY '{password}'; "
        "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION; "
        "CREATE DATABASE smartticket_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
    )
    result = mysql(sql)
    if result.returncode:
        # Never print SQL or provider output containing a password.
        raise RuntimeError("Partial init completion failed; preserving all state for inspection")
    report = {
        "completed_interrupted_initialization": True,
        "project": "smartticket-ai",
        "preexisting_business_databases": [],
        "data_deleted": False,
        "databases_created": ["smartticket_ai"],
    }
    (ROOT / "artifacts/validation/docker-partial-init-recovery.json").write_text(
        json.dumps(report, indent=2)
    )
    print(report)


if __name__ == "__main__":
    main()
