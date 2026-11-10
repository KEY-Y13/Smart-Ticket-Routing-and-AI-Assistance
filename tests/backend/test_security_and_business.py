from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select

from backend.app.db.models import AgentProfile, User

pytestmark = pytest.mark.anyio


async def test_resource_ownership_covers_detail_messages_events_business(api):
    ticket = await api.create()
    for suffix in ("", "/messages", "/events", "/business-context", "/sla"):
        response = await api.client.get(
            f"/api/v1/tickets/{ticket['id']}{suffix}", headers=api.auth("other")
        )
        assert response.status_code == 404
    listed = await api.client.get("/api/v1/tickets", headers=api.auth("other"))
    assert listed.json()["items"] == [] and listed.json()["total"] == 0
    assert (await api.client.get("/api/v1/tickets")).status_code == 401


async def test_customer_cannot_forge_owner_role_priority_or_assignment(api):
    for field, value in (("customer_id", api.users["other"]), ("priority", 0), ("role", "ADMIN")):
        response = await api.client.post(
            "/api/v1/tickets",
            headers={**api.auth("customer"), "Idempotency-Key": "forged-field-0001"},
            json={"subject": "test", "body": "我的工单请协助处理", field: value},
        )
        assert response.status_code == 422
    ticket = await api.create()
    response = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/assign",
        headers=api.auth("customer"),
        json={"expected_version": ticket["version"], "reason": "不允许自行分配"},
    )
    assert response.status_code == 403


async def test_foreign_or_nonexistent_order_not_linked(api):
    for number in ("ST202611060002", "ST999999999999"):
        response = await api.client.post(
            "/api/v1/tickets",
            headers={**api.auth("customer"), "Idempotency-Key": f"order-check-{number}"},
            json={"subject": "订单查询", "body": "请帮我查询这个订单的信息", "order_no": number},
        )
        assert response.status_code == 404 and response.json()["error"]["code"] == "ORDER_NOT_FOUND"


async def test_context_has_only_authorized_real_rows_and_explicit_simulation_label(api):
    ticket = await api.create()
    context = await api.client.get(
        f"/api/v1/tickets/{ticket['id']}/business-context", headers=api.auth("customer")
    )
    assert context.status_code == 200
    assert context.json()["source"] == "SIMULATED_BUSINESS_DATABASE"
    assert context.json()["order"]["order_no"] == "ST202611060001"
    assert context.json()["payments"][0]["status"] == "SUCCEEDED"
    assert "record_no" not in context.text and "password" not in context.text
    ticket = (await api.classify_assign(ticket))["ticket"]
    assert (
        await api.client.get(
            f"/api/v1/tickets/{ticket['id']}/business-context", headers=api.auth("agent")
        )
    ).status_code == 200
    assert (
        await api.client.get(
            f"/api/v1/tickets/{ticket['id']}/business-context", headers=api.auth("agent2")
        )
    ).status_code == 404


async def test_assigned_agent_still_needs_business_permission(api):
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
            "agent_id": str(api.users["restricted"]),
            "reason": "测试有限业务权限",
        },
    )
    assert response.status_code == 200, response.text
    assert (
        await api.client.get(
            f"/api/v1/tickets/{ticket['id']}/business-context", headers=api.auth("restricted")
        )
    ).status_code == 403


async def test_disabling_business_group_revokes_business_queries(api):
    from backend.app.db.models import BusinessGroup

    ticket = (await api.classify_assign(await api.create()))["ticket"]
    with api.app.state.database.sessions() as session:
        group = session.scalar(select(BusinessGroup).where(BusinessGroup.code == "quality"))
        group.enabled = False
        session.commit()
    response = await api.client.get(
        f"/api/v1/tickets/{ticket['id']}/business-context", headers=api.auth("agent")
    )
    assert response.status_code == 403


async def test_reassignment_revokes_old_agent_and_idempotent_message_replay(api):
    ticket = (await api.classify_assign(await api.create()))["ticket"]
    body = {"expected_version": ticket["version"], "body": "第一位客服的消息"}
    headers = {**api.auth("agent"), "Idempotency-Key": "previous-agent-001"}
    reply = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/messages", json=body, headers=headers
    )
    ticket = reply.json()["ticket"]
    result = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/assign",
        headers=api.auth("admin"),
        json={
            "expected_version": ticket["version"],
            "agent_id": str(api.users["agent2"]),
            "reason": "重新分配",
        },
    )
    assert result.status_code == 200, result.text
    assert (
        await api.client.post(
            f"/api/v1/tickets/{ticket['id']}/messages", json=body, headers=headers
        )
    ).status_code == 404
    with api.app.state.database.sessions() as session:
        assert session.get(AgentProfile, api.users["agent"]).active_load == 0
        assert session.get(AgentProfile, api.users["agent2"]).active_load == 1


async def test_logout_and_admin_disable_revoke_existing_tokens(api):
    assert (
        await api.client.post("/api/v1/auth/logout", headers=api.auth("other"))
    ).status_code == 200
    assert (await api.client.get("/api/v1/me", headers=api.auth("other"))).status_code == 401
    with api.app.state.database.sessions() as session:
        version = session.get(User, api.users["customer"]).version
    response = await api.client.patch(
        f"/api/v1/admin/users/{api.users['customer']}",
        headers=api.auth("admin"),
        json={"expected_version": version, "enabled": False},
    )
    assert response.status_code == 200
    assert (await api.client.get("/api/v1/me", headers=api.auth("customer"))).status_code == 401


async def test_tampered_expired_and_wrong_audience_tokens_rejected(api):
    settings = api.settings
    now = datetime.now(UTC)
    claims = {
        "sub": str(api.users["customer"]),
        "ver": 1,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now - timedelta(hours=2),
        "nbf": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
        "jti": "expired",
    }
    expired = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    claims["exp"] = now + timedelta(hours=1)
    claims["aud"] = "another-application"
    wrong_audience = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    for token in ("malformed.token", expired, wrong_audience):
        response = await api.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


async def test_login_errors_no_enumeration_or_hash_disclosure_and_limiter(api):
    first = await api.client.post(
        "/api/v1/auth/login", json={"login": "missing", "password": "wrong"}
    )
    second = await api.client.post(
        "/api/v1/auth/login", json={"login": "customer", "password": "wrong"}
    )
    assert first.status_code == second.status_code == 401
    assert first.json()["error"]["message"] == second.json()["error"]["message"]
    assert "hash" not in second.text and "wrong" not in second.text
    for _ in range(3):
        response = await api.client.post(
            "/api/v1/auth/login", json={"login": "missing", "password": "wrong"}
        )
    assert response.status_code == 429


async def test_sql_injection_and_pagination_limits(api):
    await api.create()
    result = await api.client.get(
        "/api/v1/tickets", params={"q": "'; DROP TABLE users; --"}, headers=api.auth("customer")
    )
    assert result.status_code == 200 and result.json()["items"] == []
    assert (await api.client.get("/api/v1/me", headers=api.auth("customer"))).status_code == 200
    assert (
        await api.client.get(
            "/api/v1/tickets", params={"page_size": 1000}, headers=api.auth("admin")
        )
    ).status_code == 422


async def test_admin_user_creation_hashes_password_and_customer_cannot_create(api):
    body = {"login": "new-customer", "password": "Not-a-real-password-2026!", "role": "CUSTOMER"}
    assert (
        await api.client.post("/api/v1/admin/users", json=body, headers=api.auth("customer"))
    ).status_code == 403
    response = await api.client.post("/api/v1/admin/users", json=body, headers=api.auth("admin"))
    assert response.status_code == 201 and "password" not in response.text
    with api.app.state.database.sessions() as session:
        user = session.scalar(select(User).where(User.login == body["login"]))
        assert user.password_hash.startswith("$argon2")


async def test_ready_is_core_only_and_missing_sla_rolls_back_ticket_creation(api):
    assert (await api.client.get("/health/ready")).status_code == 200
    # Mark existing test policies inactive; do not delete any business database objects.
    from backend.app.db.models import SLAPolicy, Ticket

    with api.app.state.database.sessions() as session:
        for policy in session.scalars(select(SLAPolicy)):
            policy.active = False
        session.commit()
    response = await api.client.post(
        "/api/v1/tickets",
        headers={**api.auth("customer"), "Idempotency-Key": "missing-policy-001"},
        json={"subject": "测试", "body": "配置缺失必须整个请求回滚"},
    )
    assert response.status_code == 503
    with api.app.state.database.sessions() as session:
        assert session.scalar(select(Ticket)) is None
    assert (await api.client.get("/health/ready")).status_code == 503
