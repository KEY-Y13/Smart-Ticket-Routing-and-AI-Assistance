"""Explicit one-shot Compose migration/bootstrap service; API never performs DDL."""

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.security import hash_password
from backend.app.db.bootstrap import initialize_policies
from backend.app.db.knowledge_demo import seed_knowledge
from backend.app.db.models import Base, JobTask, User
from backend.app.db.simulation import seed_simulation


def main():
    settings = Settings()
    value = settings.database_url.get_secret_value()
    if not value.startswith("mysql+pymysql://root:") or "@db:3306/smartticket_ai" not in value:
        raise RuntimeError("Only the isolated Compose database is permitted")
    env = {**os.environ, "SMARTTICKET_DATABASE_URL": value}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)
    engine = create_engine(value, hide_parameters=True)
    private = Path("/run/secrets")
    with Session(engine) as session:
        initialize_policies(session)
        if session.scalar(select(User.id).where(User.login == "admin")) is None:
            session.add(
                User(
                    login="admin",
                    role="ADMIN",
                    password_hash=hash_password((private / "admin_password").read_text().strip()),
                )
            )
            session.flush()
            seed_simulation(session, (private / "demo_password").read_text().strip())
        admin = session.scalar(select(User).where(User.login == "admin"))
        catalog = json.loads(Path("data/knowledge/catalog.json").read_text(encoding="utf-8"))
        if seed_knowledge(session, admin, catalog):
            session.add(
                JobTask(
                    type="BUILD_KNOWLEDGE_INDEX",
                    dedupe_key="demo-knowledge-bootstrap",
                    payload={"actor_id": admin.id},
                )
            )
        session.commit()
    with engine.begin() as connection:
        password = (private / "app_password").read_text().strip()
        exists = connection.execute(
            text("SELECT COUNT(*) FROM mysql.user WHERE User='smartticket_app' AND Host='%'")
        ).scalar()
        if not exists:
            connection.exec_driver_sql(
                "CREATE USER %s@%s IDENTIFIED BY %s", ("smartticket_app", "%", password)
            )
        for table in [*Base.metadata.tables, "alembic_version"]:
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
            if table in {
                "orders",
                "products",
                "order_items",
                "payments",
                "shipments",
                "refunds",
                "alembic_version",
            }:
                rights = "SELECT"
            connection.exec_driver_sql(
                f"GRANT {rights} ON smartticket_ai.`{table}` TO %s@%s", ("smartticket_app", "%")
            )
    engine.dispose()
    print("Compose schema and least-privilege application role ready; paid calls disabled.")


if __name__ == "__main__":
    main()
