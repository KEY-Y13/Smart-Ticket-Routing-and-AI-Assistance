"""Project-only backup drill: restore into a new uniquely named schema, never overwrite."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime

from scripts.local_mysql import connect
from scripts.provision_local_mysql import INSTANCE, MYSQL_HOME, PORT, ROOT


def snapshot(connection, database):
    result = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
            (database,),
        )
        tables = [r[0] for r in cursor.fetchall()]
        for table in tables:
            cursor.execute(
                "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
                "ORDER BY ORDINAL_POSITION",
                (database, table),
            )
            primary = [r[0] for r in cursor.fetchall()]
            order = ",".join("`" + name + "`" for name in primary)
            cursor.execute(f"SELECT * FROM `{database}`.`{table}` ORDER BY {order}")
            rows = cursor.fetchall()
            result[table] = {
                "count": len(rows),
                "sha256": hashlib.sha256(
                    json.dumps(
                        rows, default=str, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            }
    return result


def main():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    directory = INSTANCE / "backups" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    credentials = json.loads((INSTANCE / "credentials.json").read_text())
    options = directory / "client.cnf"
    options.write_text(
        f"[client]\nhost=127.0.0.1\nport={PORT}\nuser=root\npassword={credentials['root_password']}\ndefault-character-set=utf8mb4\n",
        encoding="utf-8",
    )
    target = "smartticket_ai_restore_" + stamp
    backup = directory / "smartticket_ai.sql"
    with connect() as connection:
        before = snapshot(connection, "smartticket_ai")
        with backup.open("wb") as output:
            subprocess.run(
                [
                    str(MYSQL_HOME / "bin/mysqldump.exe"),
                    f"--defaults-extra-file={options}",
                    "--single-transaction",
                    "--no-tablespaces",
                    "--set-gtid-purged=OFF",
                    "--column-statistics=0",
                    "smartticket_ai",
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                check=True,
            )
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        with backup.open("rb") as source:
            subprocess.run(
                [
                    str(MYSQL_HOME / "bin/mysql.exe"),
                    f"--defaults-extra-file={options}",
                    "--binary-mode=1",
                    target,
                ],
                stdin=source,
                capture_output=True,
                check=True,
            )
        restored = snapshot(connection, target)
        after = snapshot(connection, "smartticket_ai")
    report = {
        "at": datetime.now(UTC).isoformat(),
        "source": "smartticket_ai on verified project datadir:33307",
        "restore_database": target,
        "backup_sha256": hashlib.file_digest(backup.open("rb"), "sha256").hexdigest(),
        "source_unchanged_during_drill": before == after,
        "all_table_counts_and_hashes_match": after == restored,
        "tables": restored,
        "backup": str(backup),
        "original_mysql_3306": "untouched",
        "overwritten_databases": [],
    }
    output = ROOT / "artifacts/validation/backup-restore.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        {
            "report": str(output),
            "matched": report["all_table_counts_and_hashes_match"],
            "source_unchanged": before == after,
        }
    )
    if before != after or restored != after:
        raise RuntimeError("Backup drill mismatch; all data preserved for inspection")


if __name__ == "__main__":
    main()
