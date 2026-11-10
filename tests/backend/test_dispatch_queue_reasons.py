import pytest

pytestmark = pytest.mark.anyio


async def review(api, ticket, category):
    response = await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/classification-review",
        headers=api.auth("admin"),
        json={
            "expected_version": ticket["version"],
            "category": category,
            "reason": "测试队列原因",
        },
    )
    assert response.status_code == 200
    return response.json()


async def assign(api, ticket):
    return await api.client.post(
        f"/api/v1/tickets/{ticket['id']}/assign",
        headers=api.auth("admin"),
        json={"expected_version": ticket["version"], "reason": "规则派单"},
    )


async def test_missing_rule_and_offline_agents_are_distinguished(api):
    ticket = await review(api, await api.create(), 0)
    response = await assign(api, ticket)
    assert response.json()["queue_reason"] == "NO_ROUTING_RULE"
    ticket = await review(api, response.json()["ticket"], 4)
    for name in ("agent", "agent2", "restricted"):
        response = await api.client.patch(
            f"/api/v1/agents/{api.users[name]}/availability",
            headers=api.auth("admin"),
            json={"expected_version": 1, "online": False},
        )
        assert response.status_code == 200
    response = await assign(api, ticket)
    assert response.json()["queue_reason"] == "NO_ONLINE_AGENT"


async def test_idempotency_keys_are_case_sensitive(api):
    first = await api.create(key="Case-Sensitive-01")
    second = await api.create(key="case-sensitive-01")
    assert first["id"] != second["id"]


async def test_dispatch_next_prioritizes_urgent_tickets_and_handles_empty_queue(api):
    older = await review(api, await api.create(key="older-priority-ticket"), 4)
    urgent = await review(api, await api.create(key="newer-priority-ticket"), 4)
    response = await api.client.patch(
        f"/api/v1/tickets/{urgent['id']}",
        headers=api.auth("admin"),
        json={"expected_version": urgent["version"], "priority": 0, "reason": "紧急工单"},
    )
    assert response.status_code == 200
    response = await api.client.post(
        "/api/v1/admin/dispatch-next",
        headers=api.auth("admin"),
        json={"reason": "按优先级派下一单"},
    )
    assert (
        response.status_code == 200
        and response.json()["assignment"]["ticket"]["id"] == urgent["id"]
    )
    response = await api.client.post(
        "/api/v1/admin/dispatch-next", headers=api.auth("admin"), json={"reason": "继续派单"}
    )
    assert response.json()["assignment"]["ticket"]["id"] == older["id"]
    response = await api.client.post(
        "/api/v1/admin/dispatch-next", headers=api.auth("admin"), json={"reason": "空队列"}
    )
    assert response.json() == {"queue_state": "NO_CLASSIFIED_TICKET", "assignment": None}
    assert (
        await api.client.post(
            "/api/v1/admin/dispatch-next",
            headers=api.auth("customer"),
            json={"reason": "不允许越权触发"},
        )
    ).status_code == 403
