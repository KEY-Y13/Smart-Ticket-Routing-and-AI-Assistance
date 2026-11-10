"""Render a static initial revision from an empty in-memory metadata comparison."""

from pathlib import Path

from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from backend.app.db.models import Base, UTCDateTime


def render_type(kind, obj, autogen_context):
    if kind == "type" and isinstance(obj, UTCDateTime):
        return 'sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")'
    return False


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    target = root / "alembic/versions/0001_core.py"
    if target.exists():
        raise FileExistsError("首个迁移已经存在；不得通过重生成改写已审查的迁移")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        migration = produce_migrations(MigrationContext.configure(connection), Base.metadata)
        code = render_python_code(migration.upgrade_ops, render_item=render_type)
    engine.dispose()
    header = '''"""Initial core schema with static operations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
'''
    footer = """


def downgrade():
    raise RuntimeError("禁止自动删除业务表；回退需单独审查备份恢复方案")
"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(header + code + footer)
    print(target)
