from datetime import UTC, datetime, timedelta

import pytest

from backend.app.domain.entities import extract_order_candidates
from backend.app.domain.sla import SLAPolicy, deadlines
from backend.app.domain.tickets import (
    Actor,
    BusinessRuleError,
    Role,
    Status,
    TicketAccess,
    can_read_internal,
    can_read_ticket,
    validate_transition,
)

NOW = datetime(2026, 11, 6, tzinfo=UTC)
TICKET = TicketAccess(customer_id=1, assignee_id=2)


@pytest.mark.parametrize(
    "actor,expected",
    [
        (Actor(1, Role.CUSTOMER), True),
        (Actor(99, Role.CUSTOMER), False),
        (Actor(2, Role.AGENT), True),
        (Actor(3, Role.AGENT), False),
        (Actor(9, Role.ADMIN), True),
        (Actor(9, Role.ADMIN, enabled=False), False),
    ],
)
def test_role_and_ownership(actor, expected):
    assert can_read_ticket(actor, TICKET) is expected


def test_customer_cannot_view_internal_messages():
    assert not can_read_internal(Actor(1, Role.CUSTOMER), TICKET)
    assert can_read_internal(Actor(2, Role.AGENT), TICKET)


def test_agent_can_resolve_but_customer_cannot():
    validate_transition(
        Actor(2, Role.AGENT), TICKET, Status.PROCESSING, Status.RESOLVED, "已完成处理", now=NOW
    )
    with pytest.raises(BusinessRuleError):
        validate_transition(
            Actor(1, Role.CUSTOMER), TICKET, Status.PROCESSING, Status.RESOLVED, "自行修改", now=NOW
        )


@pytest.mark.parametrize(
    "old,new",
    [
        (Status.CLOSED, Status.PROCESSING),
        (Status.NEW, Status.RESOLVED),
        (Status.WAITING_CUSTOMER, Status.CLOSED),
        (Status.PROCESSING, Status.PROCESSING),
    ],
)
def test_illegal_transition(old, new):
    with pytest.raises(BusinessRuleError):
        validate_transition(Actor(9, Role.ADMIN), TICKET, old, new, "测试", now=NOW)


def test_reopen_window_is_inclusive_and_requires_timezone():
    actor = Actor(1, Role.CUSTOMER)
    validate_transition(
        actor,
        TICKET,
        Status.CLOSED,
        Status.PENDING_ASSIGNMENT,
        "仍有问题",
        now=NOW,
        ended_at=NOW - timedelta(days=7),
    )
    with pytest.raises(BusinessRuleError, match="超过重开期限"):
        validate_transition(
            actor,
            TICKET,
            Status.CLOSED,
            Status.PENDING_ASSIGNMENT,
            "仍有问题",
            now=NOW,
            ended_at=NOW - timedelta(days=7, microseconds=1),
        )
    with pytest.raises(BusinessRuleError, match="时区"):
        validate_transition(
            actor,
            TICKET,
            Status.CLOSED,
            Status.PENDING_ASSIGNMENT,
            "仍有问题",
            now=NOW,
            ended_at=NOW.replace(tzinfo=None),
        )


def test_transition_requires_reason_and_current_permission():
    for actor, reason in [(Actor(2, Role.AGENT), "  "), (Actor(3, Role.AGENT), "测试")]:
        with pytest.raises(BusinessRuleError):
            validate_transition(actor, TICKET, Status.PROCESSING, Status.RESOLVED, reason, now=NOW)


def test_sla_uses_supplied_policy_and_natural_elapsed_hours():
    policy = SLAPolicy(
        "test-v1", response_seconds=3600, resolution_seconds=7200, warning_seconds=300
    )
    due = deadlines(NOW, policy)
    assert due["response_due_at"] == NOW + timedelta(hours=1)
    assert due["resolution_due_at"] == NOW + timedelta(hours=2)
    with pytest.raises(ValueError, match="时区"):
        deadlines(NOW.replace(tzinfo=None), policy)


def test_invalid_sla_policy_is_rejected():
    with pytest.raises(ValueError):
        SLAPolicy("bad", 0, 3600, 60)
    with pytest.raises(ValueError):
        SLAPolicy("bad", 3600, 1800, 60)


def test_order_candidates_have_spans_but_are_never_business_facts():
    text = "请查订单 st202611060001，另一个编号不存在"
    candidates = extract_order_candidates(text)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert text[candidate.start : candidate.end].upper() == candidate.value
    assert candidate.verified is False
    assert not extract_order_candidates("ST2026110600019 和 AST202611060001 均不符合边界")
