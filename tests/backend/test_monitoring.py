from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import event, select

from backend.app.api import monitoring
from backend.app.core.observability import RequestMetrics, WorkerHeartbeat, heartbeat_status
from backend.app.db.models import (
    AIFeedback,
    AISuggestion,
    JobTask,
    KnowledgeIndex,
    RoutingRule,
    Ticket,
    TicketAssignment,
    User,
    utcnow,
)
from backend.app.services.operations import metrics


def test_request_metrics_keep_bounded_latency_and_all_status_totals():
    metrics = RequestMetrics(limit=3)
    for code, seconds in ((200, 0.1), (200, 0.2), (500, 0.3), (413, 0.4)):
        metrics.record(code, seconds)
    result = metrics.snapshot()
    assert result["requests"] == 4 and result["server_errors"] == 1
    assert result["latency_samples"] == 3 and result["p95_ms"] == 400
    assert result["status_counts"] == {"200": 2, "500": 1, "413": 1}


def test_atomic_heartbeat_failure_stale_future_and_corrupt(tmp_path):
    path = tmp_path / "worker.json"
    assert heartbeat_status(path, 180)[0] == "unknown"
    publisher = WorkerHeartbeat(path)
    publisher.write("ok")
    assert heartbeat_status(path, 180)[0] == "healthy"
    publisher.write("running")
    assert heartbeat_status(path, 180)[0] == "healthy"
    assert heartbeat_status(path, 180, now=utcnow() + timedelta(seconds=181))[0] == "degraded"
    assert heartbeat_status(path, 180, now=utcnow() - timedelta(seconds=10))[0] == "degraded"
    publisher.write("error", ValueError("DO_NOT_RECORD_PRIVATE_EXCEPTION_VALUE"))
    stored = path.read_text()
    assert "ValueError" in stored and "DO_NOT_RECORD_PRIVATE" not in stored
    assert heartbeat_status(path, 180)[0] == "degraded"
    assert not list(tmp_path.glob("*.tmp"))
    for content in ("[]", "null", "{}", '{"schema_version":1,"status":"ok","updated_at":null}'):
        path.write_text(content)
        assert heartbeat_status(path, 180)[0] == "unknown"


@pytest.mark.anyio
async def test_oversized_input_keeps_security_headers_and_is_counted_once(api):
    before = api.app.state.http_metrics.snapshot()["requests"]
    response = await api.client.post("/api/v1/tickets", content=b"x" * 400001)
    assert response.status_code == 413
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert api.app.state.http_metrics.snapshot()["requests"] == before + 1


@pytest.mark.anyio
async def test_operational_health_is_admin_only_and_no_paid_call(api, monkeypatch):
    monkeypatch.setattr(monitoring, "retrieval_status", lambda _: ("healthy", "明确模拟服务在线"))
    WorkerHeartbeat(api.settings.worker_heartbeat_path).write("ok")
    with api.app.state.database.sessions() as session:
        session.add(
            KnowledgeIndex(
                generation="a" * 32,
                manifest_hash="b" * 64,
                active=True,
                chunk_count=1,
                model_revision="EXPLICIT_TEST",
            )
        )
        session.commit()
    path = "/api/v1/admin/operations/health"
    assert (await api.client.get(path)).status_code == 401
    for role in ("customer", "agent"):
        assert (await api.client.get(path, headers=api.auth(role))).status_code == 403
    response = await api.client.get(path, headers=api.auth("admin"))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "healthy" and len(data["components"]) == 4
    assert data["llm_budget"]["reserved"] == 0
    assert data["http"]["requests"] > 0
    assert api.tokens["admin"] not in response.text and "test-only-random-key" not in response.text


@pytest.mark.anyio
async def test_worker_missing_stuck_queue_expired_lease_and_failed_job_are_alerted(
    api, monkeypatch
):
    monkeypatch.setattr(monitoring, "retrieval_status", lambda _: ("degraded", "明确模拟检索断开"))
    now = utcnow()
    with api.app.state.database.sessions() as session:
        for i, status in enumerate(("QUEUED", "RUNNING", "FAILED")):
            session.add(
                JobTask(
                    type="EXPLICIT_TEST_ONLY",
                    dedupe_key=f"monitor-{i}",
                    payload={},
                    status=status,
                    available_at=now - timedelta(seconds=130),
                    lease_until=now - timedelta(seconds=1) if status == "RUNNING" else None,
                )
            )
        session.commit()
    data = (
        await api.client.get("/api/v1/admin/operations/health", headers=api.auth("admin"))
    ).json()
    assert data["status"] == "degraded"
    assert (
        data["queue"]["queued"] == data["queue"]["expired_leases"] == data["queue"]["failed"] == 1
    )
    assert data["queue"]["oldest_due_seconds"] >= 130
    assert len(data["alerts"]) >= 5


def test_retrieval_probe_uses_authenticated_health_not_search_or_llm(monkeypatch):
    from backend.app.core.config import Settings

    settings = Settings(retrieval_secret=SecretStr("PRIVATE_TEST_TOKEN"))
    actual_client = httpx.Client
    calls = []

    def handle(request):
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer PRIVATE_TEST_TOKEN"
        return httpx.Response(200, json={"alive": True, "models_loaded": False})

    monkeypatch.setattr(
        monitoring.httpx,
        "Client",
        lambda **kw: actual_client(
            **kw,
            transport=httpx.MockTransport(handle),
        ),
    )
    status, detail = monitoring.retrieval_status(settings)
    assert status == "healthy" and "预热" in detail and calls == ["/health"]


@pytest.mark.anyio
async def test_business_metrics_query_count_does_not_grow_with_tickets_or_feedback(api):
    engine = api.app.state.database.engine

    def measure():
        statements = []

        def collect(_con, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        with api.app.state.database.sessions() as session:
            user = session.get(User, api.users["admin"])
            event.listen(engine, "before_cursor_execute", collect)
            try:
                data = metrics(session, user)
            finally:
                event.remove(engine, "before_cursor_execute", collect)
        return len(statements), data

    empty_queries, _ = measure()
    with api.app.state.database.sessions() as session:
        rule = session.scalar(select(RoutingRule))
        for i in range(20):
            ticket = Ticket(
                ticket_no=f"METRIC-TEST-{i}",
                customer_id=api.users["customer"],
                subject="明确隔离统计测试",
                status="NEW",
            )
            session.add(ticket)
            session.flush()
            draft = AISuggestion(
                ticket_id=ticket.id,
                actor_id=api.users["admin"],
                input_version=1,
                context_hash="a" * 64,
                status="ACCEPTED",
                body="明确测试草稿",
                raw_output={"is_llm": i % 2 == 0},
                evidence=[],
                facts={},
                model_version="EXPLICIT_TEST",
                prompt_version="TEST",
                latency_ms=1,
                usage={},
            )
            session.add(draft)
            session.flush()
            session.add(
                AIFeedback(
                    suggestion_id=draft.id,
                    reviewer_id=api.users["admin"],
                    decision="ACCEPTED",
                    final_body="明确测试审核",
                    reason="隔离自动化样本",
                )
            )
            if i == 0:
                for _ in range(2):
                    session.add(
                        TicketAssignment(
                            ticket_id=ticket.id,
                            agent_id=api.users["agent"],
                            group_id=rule.group_id,
                            rule_id=rule.id,
                            started_at=utcnow(),
                            ended_at=utcnow(),
                            reason="明确历史分配测试",
                        )
                    )
        session.commit()
    populated_queries, data = measure()
    assert populated_queries == empty_queries and populated_queries <= 10
    assert data["tickets"] == 20 and data["tickets_reassigned"] == 1
    assert data["ai_acceptance"] == {"numerator": 10, "denominator": 10, "rate": 1}
