from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DATETIME(fsp=6) if dialect.name == "mysql" else DateTime())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("数据库时间必须包含时区")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


PK = Integer().with_variant(BigInteger(), "mysql")
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Entity:
    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Versioned:
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    @declared_attr.directive
    def __mapper_args__(cls):
        return {"version_id_col": cls.version}


class User(Entity, Versioned, Base):
    __tablename__ = "users"
    login: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint("role IN ('CUSTOMER','AGENT','ADMIN')", name="role"),)


class DispatchMutex(Base):
    __tablename__ = "dispatch_mutex"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class AgentProfile(Versioned, Base):
    __tablename__ = "agent_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    capacity: Mapped[int] = mapped_column(Integer, default=3)
    active_load: Mapped[int] = mapped_column(Integer, default=0)
    last_assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        CheckConstraint(
            "capacity > 0 AND active_load >= 0 AND active_load <= capacity", name="capacity"
        ),
    )


class BusinessGroup(Entity, Versioned, Base):
    __tablename__ = "business_groups"
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class GroupMember(Base):
    __tablename__ = "group_members"
    group_id: Mapped[int] = mapped_column(ForeignKey("business_groups.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    can_handle: Mapped[bool] = mapped_column(Boolean, default=True)
    can_view_business: Mapped[bool] = mapped_column(Boolean, default=False)


class RoutingRule(Entity, Base):
    __tablename__ = "routing_rules"
    category: Mapped[int] = mapped_column(Integer)
    group_id: Mapped[int] = mapped_column(ForeignKey("business_groups.id"))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("category", "group_id"),
        CheckConstraint("category >= 0 AND category <= 5", name="category"),
        Index("ix_routing_category_enabled_rank", "category", "enabled", "rank"),
    )


class Product(Entity, Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class Order(Entity, Base):
    __tablename__ = "orders"
    order_no: Mapped[str] = mapped_column(String(64), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    __table_args__ = (Index("ix_orders_customer_created", "customer_id", "created_at"),)


class OrderItem(Entity, Base):
    __tablename__ = "order_items"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    product_name_snapshot: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    __table_args__ = (
        UniqueConstraint("order_id", "line_no"),
        CheckConstraint("quantity > 0 AND unit_price >= 0", name="quantity_price"),
    )


class Payment(Entity, Base):
    __tablename__ = "payments"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    record_no: Mapped[str] = mapped_column(String(64), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Shipment(Entity, Base):
    __tablename__ = "shipments"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    tracking_no: Mapped[str] = mapped_column(String(100))
    carrier: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(32))


class Refund(Entity, Base):
    __tablename__ = "refunds"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    record_no: Mapped[str] = mapped_column(String(64), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)


class Ticket(Entity, Versioned, Base):
    __tablename__ = "tickets"
    ticket_no: Mapped[str] = mapped_column(String(64), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    subject: Mapped[str] = mapped_column(String(200))
    category: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(32))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    analysis_status: Mapped[str] = mapped_column(String(32), default="NEEDS_REVIEW")
    input_version: Mapped[int] = mapped_column(Integer, default=1)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        CheckConstraint("priority BETWEEN 0 AND 3", name="priority"),
        CheckConstraint("category IS NULL OR category BETWEEN 0 AND 5", name="category"),
        CheckConstraint(
            "status IN ('NEW','PENDING_ASSIGNMENT','PROCESSING','WAITING_CUSTOMER',"
            "'WAITING_EXTERNAL','RESOLVED','CLOSED')",
            name="status",
        ),
        Index("ix_tickets_customer_created", "customer_id", "created_at", "id"),
        Index("ix_tickets_assignee_status_created", "assignee_id", "status", "created_at"),
        Index("ix_tickets_queue", "status", "priority", "created_at", "id"),
    )


class TicketMessage(Entity, Base):
    __tablename__ = "ticket_messages"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint("visibility IN ('PUBLIC','INTERNAL')", name="visibility"),)


class TicketEvent(Entity, Base):
    __tablename__ = "ticket_events"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[dict] = mapped_column(JSON, default=dict)
    new_value: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(64))


class TicketAssignment(Entity, Base):
    __tablename__ = "ticket_assignments"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    agent_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("business_groups.id"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[int] = mapped_column(ForeignKey("routing_rules.id"))
    active_ticket_id: Mapped[int | None] = mapped_column(
        PK,
        Computed("CASE WHEN ended_at IS NULL THEN ticket_id ELSE NULL END", persisted=True),
        unique=True,
    )


class SLAPolicy(Entity, Base):
    __tablename__ = "sla_policies"
    policy_code: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer)
    response_seconds: Mapped[int] = mapped_column(Integer)
    resolution_seconds: Mapped[int] = mapped_column(Integer)
    warning_seconds: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("policy_code", "version", "priority"),
        CheckConstraint(
            "response_seconds > 0 AND resolution_seconds >= response_seconds "
            "AND warning_seconds >= 0 AND warning_seconds < response_seconds",
            name="times",
        ),
    )


class TicketSLACycle(Entity, Base):
    __tablename__ = "ticket_sla_cycles"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    cycle_no: Mapped[int] = mapped_column(Integer)
    policy_id: Mapped[int] = mapped_column(ForeignKey("sla_policies.id"))
    policy_snapshot: Mapped[dict] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    response_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    resolution_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    first_response_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (UniqueConstraint("ticket_id", "cycle_no"),)


class JobTask(Entity, Base):
    __tablename__ = "job_tasks"
    type: Mapped[str] = mapped_column(String(50))
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_token: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_jobs_pending", "status", "available_at", "id"),)


class IdempotencyKey(Entity, Base):
    __tablename__ = "idempotency_keys"
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(
        String(128).with_variant(String(128, collation="utf8mb4_bin"), "mysql")
    )
    request_hash: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    __table_args__ = (UniqueConstraint("actor_id", "operation", "key"),)


class AdminEvent(Entity, Base):
    __tablename__ = "admin_events"
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[int] = mapped_column(PK)
    detail: Mapped[dict] = mapped_column(JSON)
    request_id: Mapped[str] = mapped_column(String(64))


class KnowledgeDocument(Entity, Versioned, Base):
    __tablename__ = "knowledge_documents"
    document_key: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("document_key", "revision"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint("status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')", name="status"),
        Index("ix_knowledge_status_valid", "status", "valid_from", "valid_until"),
    )


class KnowledgeChunk(Entity, Base):
    __tablename__ = "knowledge_chunks"
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"))
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("document_id", "position"),)


class KnowledgeIndex(Entity, Base):
    __tablename__ = "knowledge_indexes"
    generation: Mapped[str] = mapped_column(String(32), unique=True)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    chunk_count: Mapped[int] = mapped_column(Integer)
    model_revision: Mapped[str] = mapped_column(String(100))


class AISuggestion(Entity, Versioned, Base):
    __tablename__ = "ai_suggestions"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    input_version: Mapped[int] = mapped_column(Integer)
    context_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING_REVIEW")
    body: Mapped[str] = mapped_column(Text)
    raw_output: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON)
    facts: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    latency_ms: Mapped[int] = mapped_column(Integer)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_message_id: Mapped[int | None] = mapped_column(ForeignKey("ticket_messages.id"))
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_REVIEW','ACCEPTED','EDITED','REJECTED','SENT')", name="status"
        ),
    )


class AIFeedback(Entity, Base):
    __tablename__ = "ai_feedback"
    suggestion_id: Mapped[int] = mapped_column(ForeignKey("ai_suggestions.id"), unique=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))
    final_body: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    approved_for_training: Mapped[bool] = mapped_column(Boolean, default=False)
    knowledge_error: Mapped[bool] = mapped_column(Boolean, default=False)


class SLAAlert(Entity, Base):
    __tablename__ = "sla_alerts"
    cycle_id: Mapped[int] = mapped_column(ForeignKey("ticket_sla_cycles.id"))
    kind: Mapped[str] = mapped_column(String(30))
    due_at: Mapped[datetime] = mapped_column(UTCDateTime())
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (UniqueConstraint("cycle_id", "kind"),)


class ModelAnalysis(Entity, Base):
    __tablename__ = "model_analyses"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    input_version: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("ticket_id", "input_version"),)


class AIInvocation(Entity, Base):
    __tablename__ = "ai_invocations"
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"))
    purpose: Mapped[str] = mapped_column(String(24), default="DRAFT", server_default="DRAFT")
    data_ref: Mapped[str | None] = mapped_column(String(64))
    review_result: Mapped[dict | None] = mapped_column(JSON)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="RESERVED")
    model: Mapped[str] = mapped_column(String(100))
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(50))
    __table_args__ = (
        UniqueConstraint("purpose", "data_ref"),
        CheckConstraint(
            "(purpose = 'DRAFT' AND ticket_id IS NOT NULL AND data_ref IS NULL) OR "
            "(purpose = 'DATA_REVIEW' AND ticket_id IS NULL AND data_ref IS NOT NULL "
            "AND length(data_ref)=64)",
            name="call_reference",
        ),
    )


class ReviewCollection(Entity, Base):
    __tablename__ = "review_collections"
    code: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))
    provenance: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    imported_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        CheckConstraint("kind IN ('CLASSIFICATION','KNOWLEDGE','RETRIEVAL','LLM')", name="kind"),
    )


class ReviewItem(Entity, Versioned, Base):
    __tablename__ = "review_items"
    collection_id: Mapped[int] = mapped_column(ForeignKey("review_collections.id"))
    item_key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    __table_args__ = (
        UniqueConstraint("collection_id", "item_key"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="status"),
        Index("ix_review_items_collection_status_id", "collection_id", "status", "id"),
    )


class ReviewDecision(Entity, Base):
    __tablename__ = "review_decisions"
    item_id: Mapped[int] = mapped_column(ForeignKey("review_items.id"))
    item_version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))
    annotation: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("item_id", "item_version"),
        CheckConstraint("decision IN ('APPROVED','REJECTED')", name="decision"),
    )


class ReviewSnapshot(Entity, Base):
    __tablename__ = "review_snapshots"
    collection_id: Mapped[int] = mapped_column(ForeignKey("review_collections.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    rows: Mapped[list] = mapped_column(JSON)
    decision_ids: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)


class ModelRelease(Entity, Versioned, Base):
    __tablename__ = "model_releases"
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("review_snapshots.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    manifest_ref: Mapped[str] = mapped_column(String(500))
    fingerprints: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="CANDIDATE")
    scope: Mapped[str] = mapped_column(String(10), default="SHADOW")
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_reason: Mapped[str | None] = mapped_column(Text)
    package_ref: Mapped[str | None] = mapped_column(String(500))
    package_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("status IN ('CANDIDATE','APPROVED','REJECTED','REVOKED')", name="status"),
        CheckConstraint("scope IN ('SHADOW','LIVE')", name="scope"),
    )
