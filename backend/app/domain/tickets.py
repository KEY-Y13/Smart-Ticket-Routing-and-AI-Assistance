from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Role(StrEnum):
    CUSTOMER = "CUSTOMER"
    AGENT = "AGENT"
    ADMIN = "ADMIN"


class Status(StrEnum):
    NEW = "NEW"
    PENDING_ASSIGNMENT = "PENDING_ASSIGNMENT"
    PROCESSING = "PROCESSING"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class BusinessRuleError(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    user_id: int
    role: Role
    enabled: bool = True


@dataclass(frozen=True)
class TicketAccess:
    customer_id: int
    assignee_id: int | None


def can_read_ticket(actor: Actor, ticket: TicketAccess) -> bool:
    if not actor.enabled:
        return False
    return (
        actor.role == Role.ADMIN
        or (actor.role == Role.CUSTOMER and actor.user_id == ticket.customer_id)
        or (actor.role == Role.AGENT and actor.user_id == ticket.assignee_id)
    )


def can_read_internal(actor: Actor, ticket: TicketAccess) -> bool:
    return actor.role in (Role.AGENT, Role.ADMIN) and can_read_ticket(actor, ticket)


def validate_transition(
    actor: Actor,
    ticket: TicketAccess,
    current: Status,
    target: Status,
    reason: str,
    *,
    now: datetime,
    ended_at: datetime | None = None,
    reopen_window: timedelta = timedelta(days=7),
) -> None:
    """User actions only; NEW initialization/assignment are dedicated service commands."""
    if not can_read_ticket(actor, ticket):
        raise BusinessRuleError("无权访问该工单")
    if not reason.strip():
        raise BusinessRuleError("状态变化必须记录原因")
    if current == target:
        raise BusinessRuleError("状态没有变化")
    active = {Status.PROCESSING, Status.WAITING_CUSTOMER, Status.WAITING_EXTERNAL}
    reopening = current in (Status.RESOLVED, Status.CLOSED) and target == Status.PENDING_ASSIGNMENT
    if reopening:
        if actor.role not in (Role.CUSTOMER, Role.ADMIN):
            raise BusinessRuleError("仅客户或管理员可重新打开")
        if ended_at is None or now.tzinfo is None or ended_at.tzinfo is None:
            raise BusinessRuleError("重新打开需要有时区的解决时间与当前时间")
        if not timedelta(0) <= now - ended_at <= reopen_window:
            raise BusinessRuleError("超过重开期限，请创建关联新工单")
        return
    if current == Status.RESOLVED and target == Status.CLOSED:
        if actor.role not in (Role.CUSTOMER, Role.ADMIN):
            raise BusinessRuleError("仅客户或管理员可关闭")
        return
    if current in active and target == Status.PENDING_ASSIGNMENT and actor.role == Role.ADMIN:
        return
    if actor.role not in (Role.AGENT, Role.ADMIN):
        raise BusinessRuleError("客户不能执行该状态变更")
    allowed = {
        Status.PROCESSING: {Status.WAITING_CUSTOMER, Status.WAITING_EXTERNAL, Status.RESOLVED},
        Status.WAITING_CUSTOMER: {Status.PROCESSING},
        Status.WAITING_EXTERNAL: {Status.PROCESSING},
    }
    if target not in allowed.get(current, set()):
        raise BusinessRuleError(f"非法状态转换: {current} -> {target}")
