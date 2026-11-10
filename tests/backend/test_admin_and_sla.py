import pytest
from sqlalchemy import select

from backend.app.db.models import AdminEvent, AgentProfile, TicketEvent, TicketSLACycle

pytestmark = pytest.mark.anyio


async def test_admin_can_configure_new_agent_group_rule_and_assign(api):
    response = await api.client.post(
        "/api/v1/admin/users",
        headers=api.auth("admin"),
        json={"login": "new-agent", "password": "Tests-new-agent-2026!", "role": "AGENT"},
    )
    assert response.status_code == 201
    agent = response.json()
    response = await api.client.post(
        "/api/v1/admin/groups",
        headers=api.auth("admin"),
        json={"code": "new-group", "name": "新技能组"},
    )
    assert response.status_code == 201
    group = response.json()
    assert (
        await api.client.post(
            f"/api/v1/admin/groups/{group['id']}/members",
            headers=api.auth("admin"),
            json={"user_id": agent["id"], "can_view_business": True},
        )
    ).status_code == 201
    assert (
        await api.client.post(
            "/api/v1/admin/routing-rules",
            headers=api.auth("admin"),
            json={"category": 4, "group_id": group["id"], "rank": 0},
        )
    ).status_code == 201
    response = await api.client.patch(
        f"/api/v1/agents/{agent['id']}/availability",
        headers=api.auth("admin"),
        json={"expected_version": 1, "online": True, "capacity": 2},
    )
    assert response.status_code == 200
    ticket = await api.create()
    response = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/classification-review",
        headers=api.auth("admin"),
        json={"expected_version": ticket["version"], "category": 4, "reason": "人工分类"},
    )
    ticket = response.json()
    response = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/assign",
        headers=api.auth("admin"),
        json={
            "expected_version": ticket["version"],
            "agent_id": agent["id"],
            "reason": "新技能组指派",
        },
    )
    assert response.status_code == 200 and response.json()["ticket"]["assignee_id"] == agent["id"]
    with api.app.state.database.sessions() as session:
        assert len(list(session.scalars(select(AdminEvent)))) == 5


async def test_priority_tightens_sla_and_downgrade_does_not_extend_it(api):
    ticket = (await api.classify_assign(await api.create()))["ticket"]
    original = ticket["subject"]
    rejected = await api.client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        headers=api.auth("agent"),
        json={
            "expected_version": ticket["version"],
            "subject": "不应部分保存",
            "priority": 0,
            "reason": "越权升级",
        },
    )
    assert rejected.status_code == 403
    assert (
        await api.client.get(f"/api/v1/tickets/{ticket['id']}", headers=api.auth("customer"))
    ).json()["subject"] == original
    for priority in (0, 3):
        result = await api.client.patch(
            f"/api/v1/tickets/{ticket['id']}",
            headers=api.auth("admin"),
            json={
                "expected_version": ticket["version"],
                "priority": priority,
                "reason": "测试优先级调整",
            },
        )
        assert result.status_code == 200, result.text
        ticket = result.json()
        with api.app.state.database.sessions() as session:
            cycle = session.scalar(select(TicketSLACycle))
            assert (cycle.response_due_at - cycle.started_at).total_seconds() == 900


async def test_customer_supplement_resumes_processing_without_counting_staff_response(api):
    ticket = (await api.classify_assign(await api.create()))["ticket"]
    waiting = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/transitions",
        headers=api.auth("agent"),
        json={
            "expected_version": ticket["version"],
            "target_status": "WAITING_CUSTOMER",
            "reason": "需要更多信息",
        },
    )
    ticket = waiting.json()
    response = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/messages",
        headers={**api.auth("customer"), "Idempotency-Key": "customer-supplement-001"},
        json={"expected_version": ticket["version"], "body": "已经更换设备，问题依旧"},
    )
    assert response.status_code == 201 and response.json()["ticket"]["status"] == "PROCESSING"
    with api.app.state.database.sessions() as session:
        assert session.scalar(select(TicketSLACycle)).first_response_at is None


async def test_unclassified_assignment_attempt_is_audited_and_busy_agent_cannot_be_disabled(api):
    ticket = await api.create()
    result = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/assign",
        headers=api.auth("admin"),
        json={"expected_version": ticket["version"], "reason": "尚未分类时尝试"},
    )
    assert result.json()["queue_reason"] == "NEEDS_MANUAL_CLASSIFICATION"
    with api.app.state.database.sessions() as session:
        assert (
            session.scalar(
                select(TicketEvent).where(TicketEvent.event_type == "ASSIGNMENT_ATTEMPTED")
            )
            is not None
        )
    await api.classify_assign(ticket)
    result = await api.client.patch(
        f"/api/v1/admin/users/{api.users['agent']}",
        headers=api.auth("admin"),
        json={"expected_version": 1, "enabled": False},
    )
    assert result.status_code == 409
    with api.app.state.database.sessions() as session:
        profile = session.get(AgentProfile, api.users["agent"])
        assert profile.active_load == 1
