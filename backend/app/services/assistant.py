import json
import time

from sqlalchemy import func, select

from backend.app.core.errors import AppError
from backend.app.db.models import (
    AIFeedback,
    AIInvocation,
    AISuggestion,
    KnowledgeChunk,
    KnowledgeDocument,
    TicketMessage,
    User,
    utcnow,
)
from backend.app.schemas.intelligence import DraftView, Search
from backend.app.services import business, knowledge, llm, tickets
from rag.text import digest, suspicious


def facts(session, user, ticket, request_id):
    if ticket.order_id is None:
        return {"source": "NO_LINKED_ORDER", "order": None}
    value = business.business_context(session, user, ticket.id, request_id, commit=False)
    value.pop("queried_at", None)
    return json.loads(json.dumps(value, default=str))


def snapshot(ticket, records):
    return digest(
        json.dumps(
            {
                "input_version": ticket.input_version,
                "order_id": ticket.order_id,
                "assignee": ticket.assignee_id,
                "facts": records,
            },
            sort_keys=True,
        )
    )


def draft_view(session, item):
    view = DraftView.model_validate(item)
    feedback = session.scalar(select(AIFeedback).where(AIFeedback.suggestion_id == item.id))
    view.final_body = feedback.final_body if feedback else None
    return view


def render_draft(citations, records):
    """Render complete evidence without inventing a question-specific clarification."""
    text = "您好，以下为相关处理依据，具体适用条件请由客服核实：\n" + "\n".join(
        f"[{identifier}] {quote}" for identifier, quote in citations
    )
    if records.get("order"):
        text += (
            "\n模拟业务记录：订单 "
            + records["order"]["order_no"]
            + "，当前状态 "
            + records["order"]["status"]
            + "。"
        )
    return text


def draft(session, user, ticket_id, body, settings, request_id):
    tickets.require_role(user, "ADMIN", "AGENT")
    ticket = tickets.get_ticket(session, user, ticket_id)
    tickets.check_version(ticket, body.expected_version)
    if ticket.status in {"CLOSED", "RESOLVED"}:
        raise AppError(409, "TICKET_INACTIVE", "请先重开工单")
    if body.mode == "llm" and not settings.llm_enabled:
        raise AppError(503, "LLM_DISABLED", "付费模型未启用；证据摘录不调用大模型")
    records = facts(session, user, ticket, request_id)
    captured = snapshot(ticket, records)
    input_version = ticket.input_version
    customer_text = "\n".join(
        session.scalars(
            select(TicketMessage.body)
            .join(User, User.id == TicketMessage.author_id)
            .where(
                TicketMessage.ticket_id == ticket.id,
                User.role == "CUSTOMER",
                TicketMessage.visibility == "PUBLIC",
            )
            .order_by(TicketMessage.id)
        )
    )[-6000:]
    session.commit()
    start = time.perf_counter()
    found = knowledge.search(
        session,
        settings,
        Search(
            query=(ticket.subject + " " + customer_text)[-1000:],
            strategy="rerank" if settings.retrieval_secret else "bm25",
            top_k=3,
        ),
    )
    if not found.sufficient:
        raise AppError(409, "INSUFFICIENT_EVIDENCE", "没有足够的当前有效依据，请补充信息或人工核查")
    safe = [row for row in found.items if not suspicious(row.text)]
    usage = {}
    if body.mode == "llm":
        tickets.lock_dispatch(session)
        if (
            session.scalar(select(func.count()).select_from(AIInvocation)) or 0
        ) >= settings.llm_call_budget:
            raise AppError(409, "LLM_BUDGET_EXHAUSTED", "已达到明确授权的调用总上限")
        invocation = AIInvocation(ticket_id=ticket.id, actor_id=user.id, model=settings.llm_model)
        session.add(invocation)
        session.commit()  # durable reservation, including calls with unknown outcome
        try:
            output, usage = llm.complete(settings, customer_text, safe)
            invocation.status, invocation.usage = "SUCCEEDED", usage
            invocation.model = usage.get("provider_model", settings.llm_model)
            session.commit()
        except AppError as exc:
            invocation.status, invocation.error_code = "FAILED", exc.code
            session.commit()
            raise
        raw = output.model_dump()
        # Render the complete chunk so selecting a substring cannot remove a negation
        # or a condition (e.g. turn '不得承诺赔偿' into '承诺赔偿').
        texts = {str(row.chunk_id): row.text for row in safe}
        citations = [
            (identifier, texts[identifier])
            for identifier in dict.fromkeys(q.chunk_id for q in output.quotes)
        ]
        model = usage.get("provider_model", settings.llm_model)
        if not citations:
            raise AppError(409, "INSUFFICIENT_EVIDENCE", "模型未选择可靠依据，请人工处理")
    else:
        raw = {"mode": "EVIDENCE_EXTRACT_ONLY", "is_llm": False, "needs_human_confirmation": True}
        citations = [(str(row.chunk_id), row.text) for row in safe[:2]]
        model = "evidence-extract-v1; no LLM"
    text = render_draft(citations, records)
    # Do not hold a DB transaction during model I/O. Recheck before storing result.
    session.rollback()
    ticket = tickets.get_ticket(session, user, ticket_id, lock=True)
    if ticket.input_version != input_version or snapshot(ticket, records) != captured:
        raise AppError(409, "DRAFT_STALE", "生成期间工单变化，请重新生成")
    created = AISuggestion(
        ticket_id=ticket.id,
        actor_id=user.id,
        input_version=input_version,
        context_hash=captured,
        body=text,
        raw_output=raw,
        evidence=[r.model_dump(mode="json") for r in safe],
        facts=records,
        model_version=model,
        prompt_version=llm.PROMPT_VERSION + "/render-v2",
        latency_ms=int((time.perf_counter() - start) * 1000),
        usage=usage,
    )
    session.add(created)
    tickets.event(
        session,
        ticket,
        user,
        "DRAFT_CREATED",
        "只保存待审核草稿，不发送",
        request_id,
        new={"mode": body.mode},
    )
    session.commit()
    return draft_view(session, created)


def authorized(session, user, suggestion_id):
    tickets.require_role(user, "ADMIN", "AGENT")
    item = session.get(AISuggestion, suggestion_id)
    if item is None:
        raise AppError(404, "DRAFT_NOT_FOUND", "草稿不存在")
    ticket = tickets.get_ticket(session, user, item.ticket_id)
    return item, ticket


def freshness(session, user, item, ticket, request_id):
    value = facts(session, user, ticket, request_id)
    if item.input_version != ticket.input_version or item.context_hash != snapshot(ticket, value):
        raise AppError(409, "DRAFT_STALE", "工单或业务事实变化，请重新生成并审核")
    for evidence in item.evidence:
        doc = session.get(KnowledgeDocument, int(evidence["document_id"]))
        chunk = session.get(KnowledgeChunk, int(evidence["chunk_id"]))
        if (
            doc is None
            or not knowledge.current(doc)
            or chunk is None
            or chunk.text_hash != evidence["text_hash"]
        ):
            raise AppError(409, "EVIDENCE_STALE", "知识已失效或变更，请重新生成")


def feedback(session, user, suggestion_id, body, request_id):
    tickets.lock_dispatch(session)
    item, ticket = authorized(session, user, suggestion_id)
    if body.decision != "REJECTED":
        freshness(session, user, item, ticket, request_id)
    item = session.scalar(
        select(AISuggestion)
        .where(AISuggestion.id == suggestion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    tickets.check_version(item, body.expected_version)
    if item.status != "PENDING_REVIEW":
        raise AppError(409, "ALREADY_REVIEWED", "草稿已经审核")
    if body.decision == "EDITED" and not body.final_body:
        raise AppError(422, "FINAL_BODY_REQUIRED", "修改采纳必须提供最终文本")
    if body.knowledge_error and body.decision != "REJECTED":
        raise AppError(422, "KNOWLEDGE_ERROR", "知识有误请拒绝草稿并修订文档")
    final = (
        body.final_body
        if body.decision == "EDITED"
        else item.body
        if body.decision == "ACCEPTED"
        else ""
    )
    item.status = body.decision
    session.add(
        AIFeedback(
            suggestion_id=item.id,
            reviewer_id=user.id,
            decision=body.decision,
            final_body=final,
            reason=body.reason,
            knowledge_error=body.knowledge_error,
            approved_for_training=False,
        )
    )
    tickets.event(
        session,
        ticket,
        user,
        "DRAFT_REVIEWED",
        body.reason,
        request_id,
        new={"suggestion_id": item.id, "decision": body.decision},
    )
    session.commit()
    return draft_view(session, item)


def send(session, user, suggestion_id, body, request_id):
    tickets.lock_dispatch(session)
    item, ticket = authorized(session, user, suggestion_id)
    if item.status == "SENT":
        return draft_view(session, item)  # replay cannot create a second public message
    freshness(session, user, item, ticket, request_id)
    # Consistent actor -> ticket -> suggestion ordering with message creation.
    session.scalar(select(User).where(User.id == user.id).with_for_update())
    ticket = tickets.get_ticket(session, user, ticket.id, lock=True)
    item = session.scalar(
        select(AISuggestion)
        .where(AISuggestion.id == item.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item.status == "SENT":
        return draft_view(session, item)
    tickets.check_version(item, body.expected_version)
    tickets.check_version(ticket, body.ticket_version)
    if item.input_version != ticket.input_version:
        raise AppError(409, "DRAFT_STALE", "锁定期间工单变化，请重新生成并审核")
    if item.status not in {"ACCEPTED", "EDITED"}:
        raise AppError(409, "REVIEW_REQUIRED", "必须先完成人工审核")
    review = session.scalar(select(AIFeedback).where(AIFeedback.suggestion_id == item.id))
    item.status = "SENT"
    # Public message and suggestion link commit in the same transaction.
    message = TicketMessage(
        ticket_id=ticket.id, author_id=user.id, visibility="PUBLIC", body=review.final_body
    )
    if ticket.status in {"CLOSED", "RESOLVED"}:
        raise AppError(409, "TICKET_INACTIVE", "请先重开工单")
    session.add(message)
    session.flush()
    item.sent_message_id = message.id
    ticket.input_version += 1
    cycle = tickets.active_cycle(session, ticket)
    if cycle.first_response_at is None:
        cycle.first_response_at = utcnow()
    tickets.event(
        session,
        ticket,
        user,
        "REVIEWED_DRAFT_SENT",
        review.reason,
        request_id,
        new={"suggestion_id": item.id, "message_id": message.id},
    )
    session.commit()
    return draft_view(session, item)
