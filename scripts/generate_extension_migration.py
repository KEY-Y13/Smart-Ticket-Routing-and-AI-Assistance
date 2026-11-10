"""Freeze an additive revision by comparing the authorized project database; no schema writes."""

from pathlib import Path

from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from backend.app.core.config import Settings
from backend.app.db.models import Base
from scripts.generate_initial_migration import render_type

root = Path(__file__).resolve().parents[1]
target = root / "alembic/versions/0002_intelligence.py"
if target.exists():
    raise FileExistsError(target)
settings = Settings(_env_file=root / ".env")
engine = create_engine(settings.database_url.get_secret_value())
with engine.connect() as connection:
    migration = produce_migrations(MigrationContext.configure(connection), Base.metadata)
    # Reject unexpected destructive changes rather than rendering them into a release.
    from alembic.operations import ops

    for operation in migration.upgrade_ops.ops:
        if not isinstance(operation, (ops.CreateTableOp, ops.ModifyTableOps)):
            raise RuntimeError(
                "Unexpected non-additive migration operation: " + type(operation).__name__
            )
        if isinstance(operation, ops.ModifyTableOps) and any(
            not isinstance(child, (ops.AddColumnOp, ops.CreateIndexOp)) for child in operation.ops
        ):
            raise RuntimeError("Only added columns are permitted")
    code = render_python_code(migration.upgrade_ops, render_item=render_type)
engine.dispose()
target.write_text(
    '''"""Knowledge, drafts, SLA alerts, model analyses and durable invocation ledger."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0002_intelligence"
down_revision = "0001_core"
branch_labels = None
depends_on = None

def upgrade():
'''
    + code
    + """

def downgrade():
    raise RuntimeError("No automatic destructive downgrade; restore a reviewed backup")
""",
    encoding="utf-8",
)
print(target)
