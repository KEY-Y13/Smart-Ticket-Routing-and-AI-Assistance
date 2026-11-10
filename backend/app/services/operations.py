from datetime import timedelta

from sqlalchemy import func, select

from backend.app.db.models import (
    AIFeedback,
    AIInvocation,
    AISuggestion,
    SLAAlert,
    SLAPolicy,
    Ticket,
    TicketAssignment,
    TicketSLACycle,
    utcnow,
)
from backend.app.services.admin import audit
from backend.app.services.tickets import lock_dispatch, require_role


def scan_sla(session, *, now=None):
    now = now or utcnow()
    lock_dispatch(session)
    created = 0
    cycles = session.scalars(
        select(TicketSLACycle)
        .where(TicketSLACycle.resolved_at.is_(None))
        .order_by(TicketSLACycle.id)
    )
    for cycle in cycles:
        warning = cycle.policy_snapshot["warning_seconds"]
        for kind, due, done in (
            ("RESPONSE", cycle.response_due_at, cycle.first_response_at),
            ("RESOLUTION", cycle.resolution_due_at, cycle.resolved_at),
        ):
            if done is not None:
                continue
            suffix = (
                "BREACH"
                if now >= due
                else "WARNING"
                if now >= due - timedelta(seconds=warning)
                else None
            )
            if suffix is None:
                continue
            key = kind + "_" + suffix
            existing = session.scalar(
                select(SLAAlert.id).where(SLAAlert.cycle_id == cycle.id, SLAAlert.kind == key)
            )
            if existing is None:
                session.add(SLAAlert(cycle_id=cycle.id, kind=key, due_at=due))
                created += 1
    session.commit()
    return {"new_alerts": created, "scanned_at": now.isoformat()}


def policy(session, user, body, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    old = list(
        session.scalars(
            select(SLAPolicy).where(SLAPolicy.priority == body.priority, SLAPolicy.active.is_(True))
        )
    )
    version = (
        session.scalar(
            select(func.max(SLAPolicy.version)).where(SLAPolicy.priority == body.priority)
        )
        or 0
    ) + 1
    for row in old:
        row.active = False
    record = SLAPolicy(
        policy_code="natural-hours", version=version, **body.model_dump(exclude={"reason"})
    )
    session.add(record)
    session.flush()
    audit(
        session,
        user,
        "SLA_POLICY_CREATED",
        "sla_policy",
        record.id,
        {
            "priority": body.priority,
            "version": version,
            "reason": body.reason,
            "existing_cycles_unchanged": True,
        },
        request_id,
    )
    session.commit()
    return {"id": str(record.id), "version": version, "existing_cycles_unchanged": True}


def metrics(session, user):
    require_role(user, "ADMIN")
    now = utcnow()
    all_tickets = list(session.scalars(select(Ticket)))
    cycles = list(session.scalars(select(TicketSLACycle)))
    responses = [c for c in cycles if c.first_response_at is not None or c.response_due_at <= now]
    resolutions = [c for c in cycles if c.resolved_at is not None or c.resolution_due_at <= now]
    reviewed_rows = session.execute(
        select(AIFeedback, AISuggestion.raw_output).join(
            AISuggestion,
            AISuggestion.id == AIFeedback.suggestion_id,
        )
    ).all()
    reviewed = [row[0] for row in reviewed_rows]
    llm_reviewed = [row[0] for row in reviewed_rows if row[1].get("is_llm") is not False]
    durations = [(c.resolved_at - c.started_at).total_seconds() for c in cycles if c.resolved_at]
    response_times = [
        (c.first_response_at - c.started_at).total_seconds() for c in cycles if c.first_response_at
    ]

    def ratio(n, d):
        return {"numerator": n, "denominator": d, "rate": n / d if d else None}

    assignment_count = session.scalar(select(func.count()).select_from(TicketAssignment))
    reassigned = (
        select(TicketAssignment.ticket_id)
        .group_by(
            TicketAssignment.ticket_id,
        )
        .having(func.count() > 1)
        .subquery()
    )
    reassign_count = session.scalar(select(func.count()).select_from(reassigned))
    return {
        "source": "LOCAL_SIMULATED_WORKFLOW; NOT_PRODUCTION_EFFECT",
        "generated_at": now.isoformat(),
        "tickets": len(all_tickets),
        "by_status": {
            status: sum(t.status == status for t in all_tickets)
            for status in sorted({t.status for t in all_tickets})
        },
        "sla_response": ratio(
            sum(
                bool(c.first_response_at and c.first_response_at <= c.response_due_at)
                for c in responses
            ),
            len(responses),
        ),
        "sla_resolution": ratio(
            sum(bool(c.resolved_at and c.resolved_at <= c.resolution_due_at) for c in resolutions),
            len(resolutions),
        ),
        "ai_acceptance": ratio(
            sum(r.decision in {"ACCEPTED", "EDITED"} for r in llm_reviewed), len(llm_reviewed)
        ),
        "draft_acceptance": ratio(
            sum(r.decision in {"ACCEPTED", "EDITED"} for r in reviewed), len(reviewed)
        ),
        "reopen_rate": ratio(
            len({c.ticket_id for c in cycles if c.cycle_no > 1}), len({c.ticket_id for c in cycles})
        ),
        "average_resolution_seconds": sum(durations) / len(durations) if durations else None,
        "average_first_response_seconds": sum(response_times) / len(response_times)
        if response_times
        else None,
        "assignment_records": assignment_count,
        "tickets_reassigned": reassign_count,
        "routing_correctness": {
            "rate": None,
            "reason": "No independently reviewed assignment ground truth",
        },
        "llm_invocations": [
            {
                "ticket_id": str(r.ticket_id) if r.ticket_id is not None else None,
                "purpose": r.purpose,
                "status": r.status,
                "model": r.model,
                "usage": r.usage,
                "cost": None,
                "cost_reason": "No configured verified price snapshot; unknown is not zero",
            }
            for r in session.scalars(
                select(AIInvocation).order_by(AIInvocation.id.desc()).limit(100)
            )
        ],
        "drafts": session.scalar(select(func.count()).select_from(AISuggestion)),
    }
