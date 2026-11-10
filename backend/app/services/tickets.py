import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError
from backend.app.db.models import (
    AgentProfile,
    BusinessGroup,
    DispatchMutex,
    GroupMember,
    IdempotencyKey,
    JobTask,
    Order,
    RoutingRule,
    SLAPolicy,
    Ticket,
    TicketAssignment,
    TicketEvent,
    TicketMessage,
    TicketSLACycle,
    User,
    utcnow,
)
from backend.app.domain.sla import SLAPolicy as DomainSLAPolicy
from backend.app.domain.sla import deadlines
from backend.app.domain.tickets import (
    Actor,
    BusinessRuleError,
    Role,
    Status,
    TicketAccess,
    can_read_ticket,
    validate_transition,
)
from backend.app.schemas.business import (
    Assign,
    AssignmentResult,
    DispatchNextResult,
    MessageResult,
    MessageView,
    TicketPage,
    TicketView,
)

ACTIVE = {Status.PROCESSING, Status.WAITING_CUSTOMER, Status.WAITING_EXTERNAL}


def require_role(user: User, *roles):
    if not user.enabled or user.role not in roles:
        raise AppError(403, "FORBIDDEN", "当前角色不能执行该操作")


def actor(user: User) -> Actor:
    return Actor(user.id, Role(user.role), user.enabled)


def ticket_access(ticket: Ticket) -> TicketAccess:
    return TicketAccess(ticket.customer_id, ticket.assignee_id)


def get_ticket(session: Session, user: User, ticket_id: int, *, lock=False) -> Ticket:
    query = select(Ticket).where(Ticket.id == ticket_id)
    if lock:
        query = query.with_for_update()
    ticket = session.scalar(query.execution_options(populate_existing=True))
    if ticket is None or not can_read_ticket(actor(user), ticket_access(ticket)):
        raise AppError(404, "TICKET_NOT_FOUND", "工单不存在或不可访问")
    return ticket


def check_version(entity, expected: int):
    if entity.version != expected:
        raise AppError(409, "VERSION_CONFLICT", "记录已被更新，请刷新后重试")


def lock_dispatch(session: Session):
    if session.scalar(select(DispatchMutex).where(DispatchMutex.id == 1).with_for_update()) is None:
        raise AppError(503, "DATABASE_NOT_INITIALIZED", "派单锁尚未初始化")


def event(session, ticket, user, kind, reason, request_id, old=None, new=None):
    session.add(
        TicketEvent(
            ticket_id=ticket.id,
            actor_id=user.id if user else None,
            event_type=kind,
            old_value=old or {},
            new_value=new or {},
            reason=reason,
            request_id=request_id,
        )
    )


def ticket_view(ticket: Ticket) -> TicketView:
    return TicketView.model_validate(ticket)


def idempotency_lookup(session, user, operation, key, body):
    # Coarse per-actor serialization is intentional in this small single-database project.
    session.scalar(select(User).where(User.id == user.id).with_for_update())
    digest = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    stored = session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.actor_id == user.id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
    )
    if stored:
        if stored.request_hash != digest:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同请求")
        if stored.expires_at < utcnow():
            raise AppError(409, "IDEMPOTENCY_EXPIRED", "幂等键已过期，请使用新键")
        return stored.response_snapshot, digest
    return None, digest


def remember(session, user, operation, key, digest, response):
    session.add(
        IdempotencyKey(
            actor_id=user.id,
            operation=operation,
            key=key,
            request_hash=digest,
            response_snapshot=response.model_dump(mode="json"),
            expires_at=utcnow() + timedelta(days=7),
        )
    )


def active_cycle(session, ticket):
    cycle = session.scalar(
        select(TicketSLACycle)
        .where(TicketSLACycle.ticket_id == ticket.id)
        .order_by(TicketSLACycle.cycle_no.desc())
        .limit(1)
    )
    if cycle is None:
        raise AppError(503, "SLA_NOT_INITIALIZED", "工单缺少 SLA 周期")
    return cycle


def add_cycle(session, ticket, started_at):
    policy = session.scalar(
        select(SLAPolicy)
        .where(SLAPolicy.priority == ticket.priority, SLAPolicy.active.is_(True))
        .order_by(SLAPolicy.version.desc(), SLAPolicy.id.desc())
        .limit(1)
    )
    if policy is None:
        raise AppError(503, "SLA_NOT_CONFIGURED", "缺少当前优先级的 SLA 配置")
    domain = DomainSLAPolicy(
        f"{policy.policy_code}:{policy.version}",
        policy.response_seconds,
        policy.resolution_seconds,
        policy.warning_seconds,
    )
    number = (
        session.scalar(
            select(func.max(TicketSLACycle.cycle_no)).where(TicketSLACycle.ticket_id == ticket.id)
        )
        or 0
    )
    cycle = TicketSLACycle(
        ticket_id=ticket.id,
        cycle_no=number + 1,
        policy_id=policy.id,
        policy_snapshot={
            "code": policy.policy_code,
            "version": policy.version,
            "priority": policy.priority,
            "response_seconds": policy.response_seconds,
            "resolution_seconds": policy.resolution_seconds,
            "warning_seconds": policy.warning_seconds,
        },
        started_at=started_at,
        **deadlines(started_at, domain),
    )
    session.add(cycle)
    return cycle


def create_ticket(session, user, body, key, request_id):
    require_role(user, Role.CUSTOMER)
    replay, digest = idempotency_lookup(session, user, "create-ticket", key, body)
    if replay:
        return TicketView.model_validate(replay)
    order = None
    if body.order_no:
        order = session.scalar(
            select(Order).where(Order.order_no == body.order_no, Order.customer_id == user.id)
        )
        if order is None:
            raise AppError(404, "ORDER_NOT_FOUND", "订单不存在或不可访问")
    now = utcnow()
    ticket = Ticket(
        ticket_no=f"STK{uuid4().hex}",
        customer_id=user.id,
        order_id=order.id if order else None,
        subject=body.subject,
        status=Status.PENDING_ASSIGNMENT,
        priority=2,
        input_version=1,
        analysis_status="NEEDS_REVIEW",
        created_at=now,
    )
    session.add(ticket)
    session.flush()
    session.add(
        TicketMessage(
            ticket_id=ticket.id,
            author_id=user.id,
            visibility="PUBLIC",
            body=body.body,
            created_at=now,
        )
    )
    add_cycle(session, ticket, now)
    event(session, ticket, user, "CREATED", "客户提交工单", request_id, new={"status": "NEW"})
    event(
        session,
        ticket,
        None,
        "STATUS_CHANGED",
        "进入分类与分配队列",
        request_id,
        old={"status": "NEW"},
        new={"status": ticket.status},
    )
    session.add(
        JobTask(
            type="CLASSIFY_TICKET",
            dedupe_key=f"classify:{ticket.id}:1",
            payload={"ticket_id": ticket.id, "input_version": 1},
        )
    )
    session.flush()
    result = ticket_view(ticket)
    remember(session, user, "create-ticket", key, digest, result)
    session.commit()
    return result


def list_tickets(session, user, page, page_size, status=None, category=None, priority=None, q=None):
    conditions = []
    if user.role == Role.CUSTOMER:
        conditions.append(Ticket.customer_id == user.id)
    elif user.role == Role.AGENT:
        conditions.append(Ticket.assignee_id == user.id)
    if status is not None:
        conditions.append(Ticket.status == status)
    if category is not None:
        conditions.append(Ticket.category == category)
    if priority is not None:
        conditions.append(Ticket.priority == priority)
    if q:
        conditions.append(Ticket.subject.contains(q, autoescape=True))
    total = session.scalar(select(func.count()).select_from(Ticket).where(*conditions))
    rows = session.scalars(
        select(Ticket)
        .where(*conditions)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TicketPage(
        items=[ticket_view(row) for row in rows], total=total, page=page, page_size=page_size
    )


def release_assignment(session, ticket, now):
    assignment = session.scalar(
        select(TicketAssignment).where(
            TicketAssignment.ticket_id == ticket.id, TicketAssignment.ended_at.is_(None)
        )
    )
    if assignment:
        profile = session.get(AgentProfile, assignment.agent_id)
        if profile is None or profile.active_load < 1:
            raise AppError(409, "LOAD_INCONSISTENT", "客服负载异常，需要管理员检查")
        profile.active_load -= 1
        assignment.ended_at = now
    session.flush()  # release the generated-column unique constraint before a new assignment


def transition_ticket(session, user, ticket_id, body, request_id):
    lock_dispatch(session)
    ticket = get_ticket(session, user, ticket_id, lock=True)
    check_version(ticket, body.expected_version)
    now = utcnow()
    if user.role == Role.CUSTOMER and body.target_status not in (
        Status.CLOSED,
        Status.PENDING_ASSIGNMENT,
    ):
        raise AppError(403, "TRANSITION_FORBIDDEN", "客户只能确认关闭或重新打开自己的工单")
    try:
        validate_transition(
            actor(user),
            ticket_access(ticket),
            Status(ticket.status),
            body.target_status,
            body.reason,
            now=now,
            ended_at=ticket.ended_at,
        )
    except BusinessRuleError as exc:
        raise AppError(409, "INVALID_TRANSITION", str(exc)) from exc
    old = ticket.status
    if body.target_status in (Status.RESOLVED, Status.PENDING_ASSIGNMENT):
        release_assignment(session, ticket, now)
    if body.target_status == Status.RESOLVED:
        if active_cycle(session, ticket).first_response_at is None:
            raise AppError(409, "PUBLIC_RESPONSE_REQUIRED", "请先向客户发送公开回复再标记解决")
        ticket.ended_at = now
        active_cycle(session, ticket).resolved_at = now
    elif body.target_status == Status.PENDING_ASSIGNMENT:
        ticket.assignee_id = None
        if old in (Status.CLOSED, Status.RESOLVED):
            add_cycle(session, ticket, now)
            ticket.ended_at = None
            ticket.input_version += 1
            ticket.category = None
            ticket.analysis_status = "NEEDS_REVIEW"
            session.add(
                JobTask(
                    type="CLASSIFY_TICKET",
                    dedupe_key=f"classify:{ticket.id}:{ticket.input_version}",
                    payload={"ticket_id": ticket.id, "input_version": ticket.input_version},
                )
            )
    ticket.status = body.target_status
    event(
        session,
        ticket,
        user,
        "STATUS_CHANGED",
        body.reason,
        request_id,
        old={"status": old},
        new={"status": ticket.status},
    )
    session.commit()
    return ticket_view(ticket)


def classify_manually(session, user, ticket_id, body, request_id):
    require_role(user, Role.AGENT, Role.ADMIN)
    lock_dispatch(session)
    ticket = get_ticket(session, user, ticket_id, lock=True)
    check_version(ticket, body.expected_version)
    if ticket.status in (Status.RESOLVED, Status.CLOSED):
        raise AppError(409, "TICKET_INACTIVE", "已结束工单请先重新打开")
    old_category, old_status = ticket.category, ticket.status
    release_assignment(session, ticket, utcnow())
    ticket.category = body.category
    ticket.analysis_status = "REVIEWED_MANUALLY"
    ticket.status, ticket.assignee_id = Status.PENDING_ASSIGNMENT, None
    for job in session.scalars(
        select(JobTask).where(
            JobTask.dedupe_key == f"classify:{ticket.id}:{ticket.input_version}",
            JobTask.status == "QUEUED",
        )
    ):
        job.status = "SUPERSEDED"
    event(
        session,
        ticket,
        user,
        "CLASSIFICATION_REVIEWED",
        body.reason,
        request_id,
        old={"category": old_category},
        new={"category": ticket.category},
    )
    if old_status != ticket.status:
        event(
            session,
            ticket,
            user,
            "STATUS_CHANGED",
            body.reason,
            request_id,
            old={"status": old_status},
            new={"status": ticket.status},
        )
    session.commit()
    return ticket_view(ticket)


def assign_ticket(session, user, ticket_id, body, request_id, *, system=False):
    if not system:
        require_role(user, Role.ADMIN)
    lock_dispatch(session)
    ticket = (
        session.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
        if system
        else get_ticket(session, user, ticket_id, lock=True)
    )
    if ticket is None:
        raise AppError(404, "TICKET_NOT_FOUND", "工单不存在")
    check_version(ticket, body.expected_version)
    if ticket.status in (Status.RESOLVED, Status.CLOSED):
        raise AppError(409, "TICKET_INACTIVE", "已结束工单请先重新打开")
    if ticket.category is None:
        event(
            session,
            ticket,
            user,
            "ASSIGNMENT_ATTEMPTED",
            body.reason,
            request_id,
            new={"queue_reason": "NEEDS_MANUAL_CLASSIFICATION"},
        )
        session.commit()
        return AssignmentResult(
            ticket=ticket_view(ticket), assigned=False, queue_reason="NEEDS_MANUAL_CLASSIFICATION"
        )
    old_status, old_assignee = ticket.status, ticket.assignee_id
    if ticket.assignee_id is not None:
        release_assignment(session, ticket, utcnow())
    candidates = session.execute(
        select(AgentProfile, GroupMember, RoutingRule, User)
        .join(User, User.id == AgentProfile.user_id)
        .join(GroupMember, GroupMember.user_id == User.id)
        .join(BusinessGroup, BusinessGroup.id == GroupMember.group_id)
        .join(RoutingRule, RoutingRule.group_id == BusinessGroup.id)
        .where(
            RoutingRule.category == ticket.category,
            RoutingRule.enabled.is_(True),
            BusinessGroup.enabled.is_(True),
            GroupMember.can_handle.is_(True),
            User.enabled.is_(True),
            User.role == Role.AGENT,
        )
    ).all()
    eligible = [
        row
        for row in candidates
        if row[0].online
        and row[0].active_load < row[0].capacity
        and (body.agent_id is None or row[0].user_id == body.agent_id)
    ]
    if body.agent_id is not None and not eligible:
        raise AppError(409, "AGENT_INELIGIBLE", "指定客服无权限、不在线或容量已满")
    ticket.assignee_id = None
    chosen = min(
        eligible,
        key=lambda row: (
            row[2].rank,
            row[0].active_load / row[0].capacity,
            row[0].last_assigned_at or datetime.min.replace(tzinfo=UTC),
            row[0].user_id,
            row[1].group_id,
        ),
        default=None,
    )
    queue_reason = None
    if chosen:
        profile, member, rule, _ = chosen
        now = utcnow()
        profile.active_load += 1
        profile.last_assigned_at = now
        ticket.assignee_id, ticket.status = profile.user_id, Status.PROCESSING
        session.add(
            TicketAssignment(
                ticket_id=ticket.id,
                agent_id=profile.user_id,
                group_id=member.group_id,
                rule_id=rule.id,
                started_at=now,
                reason=body.reason,
            )
        )
    else:
        ticket.status = Status.PENDING_ASSIGNMENT
        has_rule = session.scalar(
            select(RoutingRule.id)
            .join(BusinessGroup, BusinessGroup.id == RoutingRule.group_id)
            .where(
                RoutingRule.category == ticket.category,
                RoutingRule.enabled.is_(True),
                BusinessGroup.enabled.is_(True),
            )
            .limit(1)
        )
        queue_reason = (
            "NO_ROUTING_RULE"
            if has_rule is None
            else "NO_QUALIFIED_AGENT"
            if not candidates
            else "NO_ONLINE_AGENT"
            if not any(row[0].online for row in candidates)
            else "CAPACITY_FULL"
        )
    ticket.input_version += 1
    event(
        session,
        ticket,
        user,
        "ASSIGNMENT_ATTEMPTED",
        body.reason,
        request_id,
        old={"assignee_id": old_assignee},
        new={"assignee_id": ticket.assignee_id, "queue_reason": queue_reason},
    )
    if old_status != ticket.status:
        event(
            session,
            ticket,
            user,
            "STATUS_CHANGED",
            body.reason,
            request_id,
            old={"status": old_status},
            new={"status": ticket.status},
        )
    session.commit()
    return AssignmentResult(
        ticket=ticket_view(ticket), assigned=chosen is not None, queue_reason=queue_reason
    )


def dispatch_next(session, user, body, request_id):
    require_role(user, Role.ADMIN)
    lock_dispatch(session)
    query = select(Ticket).where(
        Ticket.status == Status.PENDING_ASSIGNMENT, Ticket.category.is_not(None)
    )
    if body.category is not None:
        query = query.where(Ticket.category == body.category)
    ticket = session.scalar(
        query.order_by(Ticket.priority, Ticket.created_at, Ticket.id).limit(1).with_for_update()
    )
    if ticket is None:
        session.commit()
        return DispatchNextResult(queue_state="NO_CLASSIFIED_TICKET", assignment=None)
    result = assign_ticket(
        session,
        user,
        ticket.id,
        Assign(expected_version=ticket.version, reason=body.reason),
        request_id,
    )
    return DispatchNextResult(queue_state="ATTEMPTED", assignment=result)


def add_message(session, user, ticket_id, body, key, request_id):
    # Actor -> ticket order matches idempotent create; access is checked before replay.
    session.scalar(select(User).where(User.id == user.id).with_for_update())
    ticket = get_ticket(session, user, ticket_id, lock=True)
    require_role(user, Role.CUSTOMER, Role.AGENT, Role.ADMIN)
    if user.role == Role.CUSTOMER and body.visibility != "PUBLIC":
        raise AppError(403, "INTERNAL_MESSAGE_FORBIDDEN", "客户不能创建内部备注")
    operation = f"message:{ticket.id}"
    replay, digest = idempotency_lookup(session, user, operation, key, body)
    if replay:
        return MessageResult.model_validate(replay)
    check_version(ticket, body.expected_version)
    if ticket.status in (Status.CLOSED, Status.RESOLVED):
        raise AppError(409, "TICKET_INACTIVE", "请先重新打开工单再补充消息")
    message = TicketMessage(
        ticket_id=ticket.id, author_id=user.id, visibility=body.visibility, body=body.body
    )
    session.add(message)
    ticket.input_version += 1
    if body.visibility == "PUBLIC" and user.role in (Role.AGENT, Role.ADMIN):
        cycle = active_cycle(session, ticket)
        if cycle.first_response_at is None:
            cycle.first_response_at = utcnow()
    if user.role == Role.CUSTOMER and ticket.status == Status.WAITING_CUSTOMER:
        old = ticket.status
        ticket.status = Status.PROCESSING
        event(
            session,
            ticket,
            user,
            "STATUS_CHANGED",
            "客户补充信息，恢复处理",
            request_id,
            old={"status": old},
            new={"status": ticket.status},
        )
    event(
        session,
        ticket,
        user,
        "MESSAGE_ADDED",
        "新增消息",
        request_id,
        new={"visibility": body.visibility},
    )
    session.flush()
    result = MessageResult(message=MessageView.model_validate(message), ticket=ticket_view(ticket))
    remember(session, user, operation, key, digest, result)
    session.commit()
    return result


def patch_ticket(session, user, ticket_id, body, request_id):
    lock_dispatch(session)
    ticket = get_ticket(session, user, ticket_id, lock=True)
    check_version(ticket, body.expected_version)
    require_role(user, Role.AGENT, Role.ADMIN)
    if ticket.status in (Status.RESOLVED, Status.CLOSED):
        raise AppError(409, "TICKET_INACTIVE", "已结束工单请先重新打开")
    old = {"subject": ticket.subject, "priority": ticket.priority}
    if body.subject is not None:
        ticket.subject = body.subject
    if body.priority is not None and body.priority != ticket.priority:
        require_role(user, Role.ADMIN)
        old_priority = ticket.priority
        ticket.priority = body.priority
        if body.priority < old_priority:
            cycle = active_cycle(session, ticket)
            policy = session.scalar(
                select(SLAPolicy)
                .where(SLAPolicy.priority == body.priority, SLAPolicy.active.is_(True))
                .order_by(SLAPolicy.version.desc())
                .limit(1)
            )
            if policy is None:
                raise AppError(503, "SLA_NOT_CONFIGURED", "缺少升级优先级 SLA")
            cycle.response_due_at = min(
                cycle.response_due_at, cycle.started_at + timedelta(seconds=policy.response_seconds)
            )
            cycle.resolution_due_at = min(
                cycle.resolution_due_at,
                cycle.started_at + timedelta(seconds=policy.resolution_seconds),
            )
            # Preserve the original snapshot and append explicit tightening provenance.
            cycle.policy_snapshot = {
                **cycle.policy_snapshot,
                "tightened_by_policy_id": policy.id,
                "tightened_to_priority": body.priority,
            }
    ticket.input_version += 1
    event(
        session,
        ticket,
        user,
        "TICKET_UPDATED",
        body.reason,
        request_id,
        old=old,
        new={"subject": ticket.subject, "priority": ticket.priority},
    )
    session.commit()
    return ticket_view(ticket)
