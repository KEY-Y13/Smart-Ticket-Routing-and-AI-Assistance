"""Offline SQL is the default delivery; online migration requires an explicit named-db gate."""

import os
from pathlib import Path

from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import context
from backend.app.db.models import Base

config = context.config
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.execute("SET SESSION default_storage_engine=InnoDB")
        context.execute("SET NAMES utf8mb4")
        context.run_migrations()
else:
    local = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    value = os.environ.get("SMARTTICKET_DATABASE_URL") or local.get("SMARTTICKET_DATABASE_URL")
    if not value:
        raise RuntimeError("在线迁移需要显式设置专用数据库连接；不会使用示例连接")
    url = make_url(value)
    if url.drivername != "mysql+pymysql" or url.database not in {
        "smartticket_ai",
        "smartticket_ai_test",
    }:
        raise RuntimeError("迁移只允许专用 smartticket_ai / smartticket_ai_test")
    if os.environ.get("SMARTTICKET_ALLOW_MIGRATION") != url.database:
        raise RuntimeError("未授权迁移：先审查 SQL，再将 SMARTTICKET_ALLOW_MIGRATION 设为目标库名")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        if tables - {"alembic_version"}:
            if "alembic_version" not in tables:
                raise RuntimeError("拒绝迁移没有项目版本标识的已有数据库")
            revisions = list(connection.scalars(text("SELECT version_num FROM alembic_version")))
            if (
                len(revisions) != 1
                or ScriptDirectory.from_config(config).get_revision(revisions[0]) is None
            ):
                raise RuntimeError("已有库版本不在本项目迁移链中")
        connection.exec_driver_sql("SET SESSION default_storage_engine=InnoDB")
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
