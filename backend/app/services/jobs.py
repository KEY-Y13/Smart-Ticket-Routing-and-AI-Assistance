"""Durable leased tasks with token fencing; manual fallback never invents predictions."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from backend.app.core.errors import AppError
from backend.app.db.models import JobTask, ModelAnalysis, Ticket, TicketEvent, TicketMessage, utcnow
from backend.app.domain.entities import extract_order_candidates
from backend.app.schemas.business import Assign
from backend.app.services import reviews
from backend.app.services.tickets import assign_ticket, lock_dispatch
from nlp.inference.classifier import ArtifactClassifier


def process_one(database, settings):
    now = utcnow()
    with database.sessions() as session:
        task = session.scalar(
            select(JobTask)
            .where(
                JobTask.type == "CLASSIFY_TICKET",
                or_(
                    JobTask.status == "QUEUED",
                    (JobTask.status == "RUNNING") & (JobTask.lease_until < now),
                ),
                JobTask.available_at <= now,
            )
            .order_by(JobTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if task is None:
            return False
        token = uuid4().hex
        task.status, task.lease_token, task.lease_until = (
            "RUNNING",
            token,
            now + timedelta(minutes=2),
        )
        task.attempts += 1
        payload, task_id = task.payload, task.id
        session.commit()
    try:
        with database.sessions() as session:
            ticket = session.get(Ticket, payload["ticket_id"])
            text = (
                ticket.subject
                + "\n"
                + "\n".join(
                    session.scalars(
                        select(TicketMessage.body)
                        .where(
                            TicketMessage.ticket_id == ticket.id,
                            TicketMessage.visibility == "PUBLIC",
                        )
                        .order_by(TicketMessage.id)
                    )
                )
            )
        if settings.classifier_manifest:
            with database.sessions() as session:
                release_id = reviews.verify_managed_release(
                    session, settings, settings.classifier_manifest
                )
            result = ArtifactClassifier(settings.classifier_manifest).predict(text)
            if release_id is not None:
                result["model_release_id"] = release_id
        else:
            result = {
                "label": None,
                "needs_manual_review": True,
                "model_version": "UNCONFIGURED",
                "reason": "No deployment-approved classifier; smoke models are not deployed",
            }
        result["order_candidates"] = [
            candidate.model_dump() for candidate in extract_order_candidates(text)
        ]
        result["entity_source"] = "VALIDATED_FORMAT_CANDIDATES_ONLY; NOT_BUSINESS_FACTS"
        error = None
    except Exception as exc:
        result = {
            "label": None,
            "needs_manual_review": True,
            "model_version": "FAILED",
            "reason": "MODEL_UNAVAILABLE",
        }
        error = type(exc).__name__
    with database.sessions() as session:
        lock_dispatch(session)
        task = session.scalar(select(JobTask).where(JobTask.id == task_id).with_for_update())
        if task.lease_token != token or task.status != "RUNNING":
            return True
        if result.get("model_release_id"):
            try:
                reviews.verify_managed_release(session, settings, settings.classifier_manifest)
            except (AppError, OSError, ValueError):
                result.update(label=None, needs_manual_review=True, reason="MODEL_APPROVAL_CHANGED")
        ticket = session.scalar(
            select(Ticket).where(Ticket.id == payload["ticket_id"]).with_for_update()
        )
        if (
            ticket.input_version != payload["input_version"]
            or ticket.analysis_status == "REVIEWED_MANUALLY"
        ):
            task.status = "SUPERSEDED"
        else:
            existing = session.scalar(
                select(ModelAnalysis.id).where(
                    ModelAnalysis.ticket_id == ticket.id,
                    ModelAnalysis.input_version == ticket.input_version,
                )
            )
            if existing is None:
                session.add(
                    ModelAnalysis(
                        ticket_id=ticket.id,
                        input_version=ticket.input_version,
                        model_version=result["model_version"],
                        result=result,
                    )
                )
            ticket.analysis_status = (
                "NEEDS_REVIEW" if result["needs_manual_review"] else "CLASSIFIED"
            )
            if result["label"] is not None and not result["needs_manual_review"]:
                ticket.category = result["label"]
            task.status = "DONE_MANUAL" if result["needs_manual_review"] else "DONE"
            session.add(
                TicketEvent(
                    ticket_id=ticket.id,
                    actor_id=None,
                    event_type="ANALYSIS_COMPLETED",
                    old_value={},
                    new_value={"analysis_status": ticket.analysis_status},
                    reason="持久化任务分析，未批准模型走人工兜底",
                    request_id=token,
                )
            )
        task.last_error, task.lease_until = error, None
        session.commit()
        if task.status == "DONE" and result.get("label") is not None:
            try:
                lock_dispatch(session)
                if result.get("model_release_id"):
                    try:
                        reviews.verify_managed_release(
                            session, settings, settings.classifier_manifest
                        )
                    except (AppError, OSError, ValueError):
                        session.refresh(ticket, with_for_update=True)
                        if ticket.analysis_status == "CLASSIFIED" and ticket.assignee_id is None:
                            ticket.analysis_status = "NEEDS_REVIEW"
                            task.status = "DONE_MANUAL"
                            session.add(
                                TicketEvent(
                                    ticket_id=ticket.id,
                                    actor_id=None,
                                    event_type="MODEL_APPROVAL_CHANGED",
                                    old_value={},
                                    new_value={"analysis_status": "NEEDS_REVIEW"},
                                    reason="分类完成后审批失效，自动派单已取消，请人工复核分类",
                                    request_id=token,
                                )
                            )
                        session.commit()
                        return True
                assign_ticket(
                    session,
                    None,
                    ticket.id,
                    Assign(
                        expected_version=ticket.version,
                        reason="部署已批准分类器完成分析后自动按规则派单",
                    ),
                    token,
                    system=True,
                )
            except AppError:
                session.rollback()  # concurrent human action wins; queue remains reviewable
    return True
