import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from backend.app.db.models import AgentProfile, JobTask, Ticket, TicketAssignment, TicketSLACycle
from backend.app.main import create_app

pytestmark = pytest.mark.anyio


async def test_create_manual_assignment_reply_resolve_close_reopen(api):
    ticket = await api.create()
    assert ticket["status"] == "PENDING_ASSIGNMENT" and ticket["category"] is None
    assert ticket["customer_id"] == str(api.users["customer"])
    with api.app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobTask)) == 1
        cycle = session.scalar(select(TicketSLACycle))
        assert (cycle.response_due_at - cycle.started_at).total_seconds() == 14400
    result = await api.classify_assign(ticket)
    assert result["assigned"] is True
    ticket = result["ticket"]
    assert ticket["assignee_id"] == str(api.users["agent"])
    response = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/messages",
        headers={**api.auth("agent"), "Idempotency-Key": "agent-reply-001"},
        json={
            "expected_version": ticket["version"],
            "body": "请先检查连接与音量，我们继续为您处理",
        },
    )
    assert response.status_code == 201, response.text
    ticket = response.json()["ticket"]
    for role, target in (
        ("agent", "RESOLVED"),
        ("customer", "CLOSED"),
        ("customer", "PENDING_ASSIGNMENT"),
    ):
        response = await api.client.post(
            f"/api/v1/tickets/{ticket['id']}/transitions",
            headers=api.auth(role),
            json={
                "expected_version": ticket["version"],
                "target_status": target,
                "reason": "测试完整闭环",
            },
        )
        assert response.status_code == 200, response.text
        ticket = response.json()
    assert ticket["category"] is None and ticket["assignee_id"] is None
    with api.app.state.database.sessions() as session:
        cycles = list(session.scalars(select(TicketSLACycle).order_by(TicketSLACycle.cycle_no)))
        assert len(cycles) == 2 and cycles[0].resolved_at is not None
        assert cycles[1].first_response_at is None
        assert session.get(AgentProfile, api.users["agent"]).active_load == 0
        assignment = session.scalar(select(TicketAssignment))
        assert assignment.ended_at is not None


async def test_create_idempotency_and_payload_conflict(api):
    first = await api.create()
    second = await api.create()
    assert first == second
    response = await api.client.post(
        "/api/v1/tickets",
        headers={**api.auth("customer"), "Idempotency-Key": "new-ticket-0001"},
        json={"subject": "不同请求", "body": "同一键不能用来创建另一张工单"},
    )
    assert response.status_code == 409
    with api.app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Ticket)) == 1


async def test_stale_version_conflicts_without_overwriting(api):
    ticket = await api.create()
    first = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/classification-review",
        json={"expected_version": ticket["version"], "category": 4, "reason": "质量问题"},
        headers=api.auth("admin"),
    )
    assert first.status_code == 200
    stale = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/classification-review",
        json={"expected_version": ticket["version"], "category": 1, "reason": "旧页面"},
        headers=api.auth("admin"),
    )
    assert stale.status_code == 409
    assert (
        await api.client.get(f"/api/v1/tickets/{ticket['id']}", headers=api.auth("customer"))
    ).json()["category"] == 4


async def test_no_agent_queue_and_sequential_capacity_guard(api):
    for index in range(3):
        assigned = await api.classify_assign(await api.create(key=f"capacity-{index:04}"))
        assert assigned["assigned"]
    fourth = await api.classify_assign(await api.create(key="capacity-0004"))
    assert fourth["assigned"] is False and fourth["queue_reason"] == "CAPACITY_FULL"
    with api.app.state.database.sessions() as session:
        assert all(p.active_load == 1 for p in session.scalars(select(AgentProfile)))
    # This is sequential capacity evidence, NOT proof of MySQL concurrent row locking.


async def test_resolve_requires_public_response_and_rolls_back_release(api):
    ticket = (await api.classify_assign(await api.create()))["ticket"]
    response = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/transitions",
        headers=api.auth("agent"),
        json={
            "expected_version": ticket["version"],
            "target_status": "RESOLVED",
            "reason": "尚未回复",
        },
    )
    assert response.status_code == 409
    with api.app.state.database.sessions() as session:
        assert session.get(AgentProfile, api.users["agent"]).active_load == 1
        assert session.scalar(select(TicketAssignment)).ended_at is None


async def test_internal_note_is_not_first_response_and_is_not_visible_to_customer(api):
    ticket = (await api.classify_assign(await api.create()))["ticket"]
    body = {
        "expected_version": ticket["version"],
        "body": "内部分析：仅客服可见",
        "visibility": "INTERNAL",
    }
    headers = {**api.auth("agent"), "Idempotency-Key": "internal-note-001"}
    first = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/messages", json=body, headers=headers
    )
    replay = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/messages", json=body, headers=headers
    )
    assert first.status_code == replay.status_code == 201 and first.json() == replay.json()
    visible = await api.client.get(
        f"/api/v1/tickets/{ticket['id']}/messages", headers=api.auth("customer")
    )
    assert len(visible.json()["items"]) == 1
    with api.app.state.database.sessions() as session:
        assert session.scalar(select(TicketSLACycle)).first_response_at is None


async def test_persistence_across_new_app_instance(api):
    ticket = await api.create()
    restarted = create_app(api.settings)
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/tickets/{ticket['id']}", headers=api.auth("customer"))
        assert response.status_code == 200 and response.json()["ticket_no"] == ticket["ticket_no"]
    restarted.state.database.dispose()
