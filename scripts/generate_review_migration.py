"""Generate a fixed, additive governance revision; never modifies a database."""

from pathlib import Path

from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from alembic.operations import ops
from sqlalchemy import create_engine

from backend.app.core.config import Settings
from backend.app.db.models import Base
from scripts.generate_initial_migration import render_type

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / "alembic/versions/0003_reviews.py"
if target.exists():
    raise FileExistsError("Existing reviewed migration must not be regenerated")
settings = Settings(_env_file=ROOT / ".env")
engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
with engine.connect() as connection:
    migration = produce_migrations(MigrationContext.configure(connection), Base.metadata)
    expected = {
        "review_collections",
        "review_items",
        "review_decisions",
        "review_snapshots",
        "model_releases",
    }
    created = {
        op.table_name for op in migration.upgrade_ops.ops if isinstance(op, ops.CreateTableOp)
    }
    if created != expected or any(
        not isinstance(op, ops.CreateTableOp)
        and not (
            isinstance(op, ops.ModifyTableOps)
            and op.table_name in expected
            and all(isinstance(child, ops.CreateIndexOp) for child in op.ops)
        )
        for op in migration.upgrade_ops.ops
    ):
        raise RuntimeError("Only the five new review tables may be generated")
    code = render_python_code(migration.upgrade_ops, render_item=render_type)
engine.dispose()
target.write_text(
    '''"""Human review decisions, frozen datasets and model release approvals."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0003_reviews"
down_revision = "0002_intelligence"
branch_labels = None
depends_on = None

def upgrade():
'''
    + code
    + """

def downgrade():
    raise RuntimeError("No automatic destructive downgrade; preserve review evidence")
""",
    encoding="utf-8",
)
print(target)
