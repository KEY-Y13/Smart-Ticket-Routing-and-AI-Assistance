"""Separate MySQL lock tests. Require approval and a migrated, isolated test database."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.core.security import hash_password
from backend.app.db.models import (
    AgentProfile,
    AIInvocation,
    BusinessGroup,
    GroupMember,
    ReviewDecision,
    ReviewItem,
    ReviewSnapshot,
    RoutingRule,
    Ticket,
    TicketAssignment,
    User,
    utcnow,
)
from backend.app.db.runtime import Database
from backend.app.schemas.business import Assign, ClassificationReview, TicketCreate, Transition
from backend.app.schemas.reviews import CollectionCreate, DecisionCreate, Freeze
from backend.app.services import assistant, reviews, tickets


@pytest.fixture
def mysql_case():
    dsn = os.environ.get("SMARTTICKET_MYSQL_TEST_URL")
    if not dsn or os.environ.get("SMARTTICKET_MYSQL_TEST_ACK") != "smartticket_ai_test":
        raise pytest.UsageError("仅在授权新建并迁移隔离测试库后设置 MySQL 测试 URL 和具名确认")
    settings = Settings(
        env="test", database_url=dsn, jwt_secret="mysql-tests-only-0123456789-abcdefgh"
    )
    database = Database(settings)
    if (
        database.engine.dialect.name != "mysql"
        or database.engine.url.database != "smartticket_ai_test"
    ):
        raise pytest.UsageError("并发用例严格拒绝其他数据库或 SQLite")
    suffix = uuid4().hex[:12]
    test_hash = hash_password(uuid4().hex)
    with database.sessions() as session:
        users = []
        for name, role in (("admin", "ADMIN"), ("customer", "CUSTOMER"), ("agent", "AGENT")):
            user = User(
                login=f"concurrency-{name}-{suffix}",
                role=role,
                password_hash=test_hash,
            )
            session.add(user)
            session.flush()
            users.append(user.id)
        group = BusinessGroup(code=f"test-{suffix}", name="isolated concurrency fixture")
        session.add(group)
        session.flush()
        session.add(AgentProfile(user_id=users[2], online=True, capacity=1))
        session.add(GroupMember(group_id=group.id, user_id=users[2]))
        session.add(RoutingRule(category=4, group_id=group.id))
        session.commit()
    yield database, users
    # Preserve uniquely named test rows for inspection; no DELETE/DROP/TRUNCATE.
    database.dispose()


def prepare_ticket(database, users, key):
    with database.sessions() as session:
        created = tickets.create_ticket(
            session,
            session.get(User, users[1]),
            TicketCreate(subject="并发测试", body="隔离数据库的明确模拟工单"),
            key,
            "mysql-test",
        )
        reviewed = tickets.classify_manually(
            session,
            session.get(User, users[0]),
            created.id,
            ClassificationReview(expected_version=created.version, category=4, reason="测试分类"),
            "mysql-test",
        )
        return reviewed.id, reviewed.version


def test_concurrent_item_review_keeps_one_decision_per_version(mysql_case):
    database, users = mysql_case
    with database.sessions() as session:
        collection = reviews.create_collection(
            session,
            session.get(User, users[0]),
            CollectionCreate(
                code="review-race-" + uuid4().hex,
                title="隔离并发审核测试",
                kind="LLM",
                provenance="EXPLICIT_MYSQL_TEST_FIXTURE_NOT_HUMAN_REVIEW",
                items=[{"id": "case-1", "question": "明确的模拟审核问题"}],
            ),
            "mysql-review-race",
        )
        item = session.scalar(
            select(ReviewItem).where(ReviewItem.collection_id == int(collection.id))
        )
        item_id, version = item.id, item.version
    barrier = Barrier(2)

    def attempt(_):
        with database.sessions() as session:
            user = session.get(User, users[0])
            barrier.wait(timeout=15)
            try:
                reviews.decide(
                    session,
                    user,
                    item_id,
                    DecisionCreate(
                        expected_version=version,
                        decision="APPROVED",
                        annotation={"verdict": "UNDETERMINED"},
                        personally_reviewed=True,
                        reason="隔离自动化模拟审核并发一致性测试",
                    ),
                    "mysql-review-race",
                )
                return "saved"
            except AppError as exc:
                assert exc.status == 409
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sorted(results) == ["VERSION_CONFLICT", "saved"]
    with database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReviewDecision)
                .where(
                    ReviewDecision.item_id == item_id,
                )
            )
            == 1
        )
        assert session.get(ReviewItem, item_id).version == version + 1


def test_old_repeatable_read_snapshot_cannot_freeze_or_validate_changed_review(mysql_case):
    database, users = mysql_case
    with database.sessions() as session:
        admin = session.get(User, users[0])
        collection = reviews.create_collection(
            session,
            admin,
            CollectionCreate(
                code="review-stale-" + uuid4().hex,
                title="隔离事务快照测试",
                kind="LLM",
                provenance="EXPLICIT_MYSQL_TEST_FIXTURE_NOT_HUMAN_REVIEW",
                items=[{"id": "case-1", "question": "模拟事务快照一致性核对"}],
            ),
            "mysql-review-snapshot",
        )
        item = session.scalar(
            select(ReviewItem).where(ReviewItem.collection_id == int(collection.id))
        )
        item_id = item.id
        reviews.decide(
            session,
            admin,
            item_id,
            DecisionCreate(
                expected_version=1,
                decision="APPROVED",
                annotation={"verdict": "ACCEPTABLE"},
                personally_reviewed=True,
                reason="隔离测试初始模拟判断",
            ),
            "mysql-review-snapshot",
        )
        frozen = reviews.freeze(
            session,
            admin,
            int(collection.id),
            Freeze(
                reason="隔离测试初始冻结版本",
                acknowledged=True,
            ),
            "mysql-review-snapshot",
        )
    with database.sessions() as reader:
        reader.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        admin = reader.get(User, users[0])
        snapshot = reader.get(ReviewSnapshot, int(frozen.id))
        assert reader.get(ReviewItem, item_id).version == 2
        with database.sessions() as writer:
            reviews.decide(
                writer,
                writer.get(User, users[0]),
                item_id,
                DecisionCreate(
                    expected_version=2,
                    decision="APPROVED",
                    annotation={"verdict": "INCORRECT"},
                    personally_reviewed=True,
                    reason="隔离测试模拟更正原判断",
                ),
                "mysql-review-snapshot",
            )
        assert not reviews.snapshot_current(reader, snapshot)
        with pytest.raises(AppError) as failed:
            reviews.freeze(
                reader,
                admin,
                int(collection.id),
                Freeze(
                    reason="过期事务禁止冻结旧标注",
                    acknowledged=True,
                ),
                "mysql-review-snapshot",
            )
        assert failed.value.code == "REVIEW_EVIDENCE_MISMATCH"
        reader.rollback()


def test_only_one_last_paid_slot_and_failure_remains_counted(mysql_case, monkeypatch):
    from backend.app.schemas.intelligence import DraftRequest, Evidence, SearchResult

    database, users = mysql_case
    records = [prepare_ticket(database, users, f"llm-slot-{uuid4().hex}") for _ in range(2)]
    with database.sessions() as session:
        before = session.scalar(select(func.count()).select_from(AIInvocation))
    settings = Settings(
        llm_enabled=True, llm_api_key="MOCK_TRANSPORT_ONLY", llm_call_budget=before + 1
    )
    evidence = Evidence(
        chunk_id=1,
        document_id=1,
        title="模拟证据",
        revision=1,
        text="测试模型超时，不执行任何真实付费请求。",
        text_hash="a" * 64,
        source="EXPLICIT_MYSQL_TEST_FIXTURE",
        score=1,
    )
    monkeypatch.setattr(
        assistant.knowledge,
        "search",
        lambda *args: SearchResult(
            generation=None,
            strategy="bm25",
            items=[evidence],
            latency_ms=0,
            sufficient=True,
            reason=None,
        ),
    )
    calls = []

    def fail_without_network(*args):
        calls.append("explicit mock only")
        raise AppError(504, "LLM_TIMEOUT", "明确模拟超时；不发送网络请求")

    monkeypatch.setattr(assistant.llm, "complete", fail_without_network)
    barrier = Barrier(2)

    def attempt(record):
        with database.sessions() as session:
            barrier.wait(timeout=10)
            try:
                assistant.draft(
                    session,
                    session.get(User, users[0]),
                    record[0],
                    DraftRequest(expected_version=record[1], mode="llm"),
                    settings,
                    "mysql-llm-budget-test",
                )
            except AppError as exc:
                session.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, records))
    assert sorted(results) == ["LLM_BUDGET_EXHAUSTED", "LLM_TIMEOUT"]
    assert len(calls) == 1
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AIInvocation)) == before + 1
        last = session.scalar(select(AIInvocation).order_by(AIInvocation.id.desc()))
        assert last.status == "FAILED" and last.error_code == "LLM_TIMEOUT"


def test_two_tickets_compete_for_last_agent_slot(mysql_case):
    database, users = mysql_case
    records = [prepare_ticket(database, users, f"slot-{uuid4().hex}") for _ in range(2)]
    barrier = Barrier(2)

    def attempt(record):
        with database.sessions() as session:
            user = session.get(User, users[0])
            barrier.wait(timeout=10)
            try:
                result = tickets.assign_ticket(
                    session,
                    user,
                    record[0],
                    Assign(expected_version=record[1], agent_id=users[2], reason="并发容量测试"),
                    "mysql-test",
                )
                return 200 if result.assigned else 202
            except AppError as exc:
                session.rollback()
                return exc.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, records))
    assert sorted(results) == [200, 409]
    with database.sessions() as session:
        assert session.get(AgentProfile, users[2]).active_load == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(TicketAssignment)
                .where(TicketAssignment.agent_id == users[2], TicketAssignment.ended_at.is_(None))
            )
            == 1
        )


def test_two_stale_status_writes_have_one_winner(mysql_case):
    database, users = mysql_case
    ticket_id, version = prepare_ticket(database, users, f"status-{uuid4().hex}")
    with database.sessions() as session:
        assigned = tickets.assign_ticket(
            session,
            session.get(User, users[0]),
            ticket_id,
            Assign(expected_version=version, agent_id=users[2], reason="准备状态测试"),
            "mysql-test",
        )
        version = assigned.ticket.version
    barrier = Barrier(2)

    def attempt(target):
        with database.sessions() as session:
            user = session.get(User, users[2])
            barrier.wait(timeout=10)
            try:
                tickets.transition_ticket(
                    session,
                    user,
                    ticket_id,
                    Transition(
                        expected_version=version, target_status=target, reason="并发状态测试"
                    ),
                    "mysql-test",
                )
                return 200
            except AppError as exc:
                session.rollback()
                return exc.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("WAITING_CUSTOMER", "WAITING_EXTERNAL")))
    assert sorted(results) == [200, 409]


def test_concurrent_idempotent_create_has_one_ticket(mysql_case):
    database, users = mysql_case
    barrier, key = Barrier(2), f"create-{uuid4().hex}"

    def attempt(_):
        with database.sessions() as session:
            user = session.get(User, users[1])
            barrier.wait(timeout=10)
            return tickets.create_ticket(
                session,
                user,
                TicketCreate(subject="幂等测试", body="并发重复请求不应重复建单"),
                key,
                "mysql-test",
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(attempt, range(2)))
    assert ids[0] == ids[1]
    with database.sessions() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.customer_id == users[1])
            )
            == 1
        )


def test_concurrent_reviewed_draft_send_creates_one_public_message(mysql_case):
    from backend.app.db.models import TicketMessage
    from backend.app.schemas.intelligence import (
        DraftRequest,
        Feedback,
        KnowledgeAction,
        KnowledgeCreate,
        SendDraft,
    )
    from backend.app.services import assistant, knowledge

    database, users = mysql_case
    ticket_id, version = prepare_ticket(database, users, f"draft-{uuid4().hex}")
    with database.sessions() as session:
        admin = session.get(User, users[0])
        doc = knowledge.create(
            session,
            admin,
            KnowledgeCreate(
                document_key="mysql-draft-" + uuid4().hex,
                title="并发测试工单排查",
                content="隔离数据库的明确模拟工单需要人工核对，测试流程不执行资金操作。",
                source="MYSQL_CONCURRENCY_TEST_FIXTURE",
                valid_from=utcnow(),
            ),
            "mysql-draft",
        )
        for action in ("review", "publish"):
            doc = knowledge.act(
                session,
                admin,
                doc.id,
                KnowledgeAction(expected_version=doc.version, action=action, reason="明确测试知识"),
                "mysql-draft",
            )
        settings = Settings(
            env="test",
            database_url=os.environ["SMARTTICKET_MYSQL_TEST_URL"],
            jwt_secret="mysql-tests-only-0123456789-abcdefgh",
        )
        created = assistant.draft(
            session,
            admin,
            ticket_id,
            DraftRequest(expected_version=version),
            settings,
            "mysql-draft",
        )
        reviewed = assistant.feedback(
            session,
            admin,
            created.id,
            Feedback(
                expected_version=created.version,
                decision="EDITED",
                final_body="明确的人工审核测试回复，不包含业务承诺。",
                reason="MySQL并发发送验收",
            ),
            "mysql-draft",
        )
        payload = SendDraft(expected_version=reviewed.version, ticket_version=version)
    barrier = Barrier(2)

    def send(_):
        with database.sessions() as session:
            admin = session.get(User, users[0])
            barrier.wait(timeout=15)
            return assistant.send(
                session, admin, created.id, payload, "mysql-draft"
            ).sent_message_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(send, range(2)))
    assert ids[0] == ids[1]
    with database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TicketMessage)
                .where(
                    TicketMessage.ticket_id == ticket_id,
                    TicketMessage.author_id == users[0],
                    TicketMessage.visibility == "PUBLIC",
                )
            )
            == 1
        )
