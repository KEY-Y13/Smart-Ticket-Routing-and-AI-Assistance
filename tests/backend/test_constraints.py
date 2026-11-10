import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from backend.app.core.errors import AppError
from backend.app.core.security import LoginLimiter
from backend.app.db.models import Base, Ticket, TicketAssignment, User, utcnow


def test_static_revision_matches_current_metadata_on_isolated_memory_database():
    paths = sorted((Path(__file__).resolve().parents[2] / "alembic/versions").glob("000*.py"))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for path in paths:
                spec = importlib.util.spec_from_file_location(path.stem, path)
                revision = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(revision)
                revision.upgrade()
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables)
        for name, table in Base.metadata.tables.items():
            assert {c["name"] for c in inspector.get_columns(name)} == set(table.columns.keys())
    engine.dispose()


def test_limiter_bounds_addresses_and_expired_entries_can_be_reused(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("backend.app.core.security.monotonic", lambda: clock[0])
    limiter = LoginLimiter(limit=1, window=60, max_addresses=2)
    limiter.check("one")
    limiter.check("two")
    with pytest.raises(AppError) as error:
        limiter.check("three")
    assert error.value.status == 429 and len(limiter.entries) == 2
    clock[0] = 61
    limiter.check("three")
    assert len(limiter.entries) == 1


@pytest.mark.anyio
async def test_generated_unique_constraint_rejects_two_active_assignments(api):
    ticket = (await api.classify_assign(await api.create()))["ticket"]
    with api.app.state.database.sessions() as session:
        current = session.scalar(select(TicketAssignment))
        session.add(
            TicketAssignment(
                ticket_id=int(ticket["id"]),
                agent_id=api.users["agent2"],
                group_id=current.group_id,
                rule_id=current.rule_id,
                started_at=utcnow(),
                reason="constraint test",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        assert len(list(session.scalars(select(TicketAssignment)))) == 1


@pytest.mark.anyio
async def test_orm_version_guard_rejects_stale_session_even_without_service_check(api):
    ticket = await api.create()
    with (
        Session(api.app.state.database.engine) as first,
        Session(api.app.state.database.engine) as second,
    ):
        a, b = first.get(Ticket, int(ticket["id"])), second.get(Ticket, int(ticket["id"]))
        a.subject = "第一会话保存"
        first.commit()
        b.subject = "旧会话不能覆盖"
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
    with api.app.state.database.sessions() as session:
        assert session.get(Ticket, int(ticket["id"])).subject == "第一会话保存"


@pytest.mark.anyio
async def test_role_and_foreign_key_constraints_are_database_enforced(api):
    with api.app.state.database.sessions() as session:
        session.add(User(login="invalid-role", role="SUPERUSER", password_hash="unused"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        session.add(
            Ticket(ticket_no="bad-fk", customer_id=999999999, subject="invalid", status="NEW")
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
