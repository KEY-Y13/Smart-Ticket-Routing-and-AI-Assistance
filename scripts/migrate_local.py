"""Apply reviewed additive migrations to the authorized local instance and extend table grants."""

import json
import os
import secrets
import subprocess

from scripts.local_mysql import connect
from scripts.provision_local_mysql import INSTANCE, ROOT, dsn


def main():
    credentials = json.loads((INSTANCE / "credentials.json").read_text())
    # Credentials and datadir are verified before making any schema changes.
    with connect() as connection:
        for database in ("smartticket_ai", "smartticket_ai_test"):
            env = {
                **os.environ,
                "SMARTTICKET_DATABASE_URL": dsn("root", credentials["root_password"], database),
                "SMARTTICKET_ALLOW_MIGRATION": database,
            }
            result = subprocess.run(
                [str(ROOT / ".conda/python.exe"), "-X", "utf8", "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            (INSTANCE / f"migration-{database}-extension.log").write_text(
                result.stdout + result.stderr, encoding="utf-8"
            )
            if result.returncode:
                raise RuntimeError("Migration failed; preserving schema and private logs")
            name = "smartticket_app" if database == "smartticket_ai" else "smartticket_test"
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
                    (database,),
                )
                for (table,) in cursor.fetchall():
                    rights = "SELECT,INSERT,UPDATE"
                    if table in {
                        "ticket_events",
                        "admin_events",
                        "knowledge_chunks",
                        "ai_feedback",
                        "model_analyses",
                        "review_collections",
                        "review_decisions",
                        "review_snapshots",
                    }:
                        rights = "SELECT,INSERT"
                    if table == "alembic_version" or (
                        name == "smartticket_app"
                        and table
                        in {"orders", "products", "order_items", "payments", "shipments", "refunds"}
                    ):
                        rights = "SELECT"
                    cursor.execute(
                        f"GRANT {rights} ON `{database}`.`{table}` TO '{name}'@'localhost'"
                    )
    path = ROOT / ".env"
    value = path.read_text(encoding="utf-8")
    if "SMARTTICKET_RETRIEVAL_SECRET=" not in value:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("SMARTTICKET_RETRIEVAL_SECRET=" + secrets.token_urlsafe(32) + "\n")
    print({"migration": "head", "databases": 2, "original_mysql": "untouched"})


if __name__ == "__main__":
    main()
