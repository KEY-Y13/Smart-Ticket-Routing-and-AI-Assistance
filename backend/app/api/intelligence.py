from typing import Annotated

from fastapi import APIRouter, Body, Header, Query, Request
from sqlalchemy import or_, select

from backend.app.api.dependencies import CurrentUser, SessionDep
from backend.app.api.routes import PositiveId
from backend.app.core.errors import AppError
from backend.app.db.models import (
    AdminEvent,
    AgentProfile,
    AISuggestion,
    BusinessGroup,
    GroupMember,
    JobTask,
    KnowledgeDocument,
    ModelAnalysis,
    RoutingRule,
    SLAAlert,
    SLAPolicy,
    Ticket,
    TicketSLACycle,
    User,
    utcnow,
)
from backend.app.schemas import business as b
from backend.app.schemas import intelligence as s
from backend.app.services import admin, assistant, knowledge, operations, tickets

router = APIRouter(prefix="/api/v1", tags=["intelligence"])


@router.post("/knowledge", status_code=201)
def create_knowledge(
    body: s.KnowledgeCreate, user: CurrentUser, session: SessionDep, request: Request
) -> s.KnowledgeView:
    return knowledge.create(session, user, body, request.state.request_id)


@router.post("/knowledge/upload", status_code=201)
def upload_knowledge(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    content: Annotated[str, Body(media_type="text/plain", min_length=10, max_length=100000)],
    filename: Annotated[str, Header(alias="X-Filename", pattern=r"^[a-zA-Z0-9_-]+\.(txt|md)$")],
    title: Annotated[str, Header(alias="X-Title", max_length=200)],
) -> s.KnowledgeView:
    # FastAPI text bodies decode UTF-8; only inert plain text/Markdown is accepted.
    body = s.KnowledgeCreate(
        document_key=filename.rsplit(".", 1)[0].casefold(),
        title=title,
        content=content,
        source="ADMIN_TEXT_UPLOAD:" + filename,
        valid_from=utcnow(),
    )
    return knowledge.create(session, user, body, request.state.request_id)


@router.get("/knowledge")
def list_knowledge(
    user: CurrentUser, session: SessionDep, page: Annotated[int, Query(ge=1, le=10000)] = 1
) -> list[s.KnowledgeView]:
    tickets.require_role(user, "ADMIN", "AGENT")
    query = select(KnowledgeDocument)
    if user.role != "ADMIN":
        query = query.where(
            KnowledgeDocument.status == "PUBLISHED",
            KnowledgeDocument.valid_from <= utcnow(),
            or_(KnowledgeDocument.valid_until.is_(None), KnowledgeDocument.valid_until > utcnow()),
        )
    return [
        s.KnowledgeView.model_validate(r)
        for r in session.scalars(
            query.order_by(KnowledgeDocument.id.desc()).offset((page - 1) * 50).limit(50)
        )
    ]


@router.post("/knowledge/{document_id}/actions")
def act_knowledge(
    document_id: PositiveId,
    body: s.KnowledgeAction,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> s.KnowledgeView:
    return knowledge.act(session, user, document_id, body, request.state.request_id)


@router.post("/knowledge/search")
def search_knowledge(
    body: s.Search, user: CurrentUser, session: SessionDep, request: Request
) -> s.SearchResult:
    tickets.require_role(user, "ADMIN", "AGENT")
    return knowledge.search(session, request.app.state.settings, body)


@router.post("/knowledge/index")
def index_knowledge(user: CurrentUser, session: SessionDep, request: Request) -> dict:
    return knowledge.build_index(
        session, user, request.app.state.settings, request.state.request_id
    )


@router.get("/tickets/{ticket_id}/analysis")
def analysis(ticket_id: PositiveId, user: CurrentUser, session: SessionDep) -> dict:
    tickets.require_role(user, "ADMIN", "AGENT")
    ticket = tickets.get_ticket(session, user, ticket_id)
    item = session.scalar(
        select(ModelAnalysis).where(
            ModelAnalysis.ticket_id == ticket.id,
            ModelAnalysis.input_version == ticket.input_version,
        )
    )
    return {
        "input_version": ticket.input_version,
        "analysis_status": ticket.analysis_status,
        "result": item.result if item else None,
        "note": "Smoke models are not deployment-approved; manual review remains available",
    }


@router.post("/tickets/{ticket_id}/assistant/draft", status_code=201)
def draft(
    ticket_id: PositiveId,
    body: s.DraftRequest,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> s.DraftView:
    return assistant.draft(
        session, user, ticket_id, body, request.app.state.settings, request.state.request_id
    )


@router.get("/tickets/{ticket_id}/assistant/drafts")
def drafts(ticket_id: PositiveId, user: CurrentUser, session: SessionDep) -> list[s.DraftView]:
    tickets.require_role(user, "ADMIN", "AGENT")
    tickets.get_ticket(session, user, ticket_id)
    return [
        assistant.draft_view(session, r)
        for r in session.scalars(
            select(AISuggestion)
            .where(AISuggestion.ticket_id == ticket_id)
            .order_by(AISuggestion.id.desc())
            .limit(30)
        )
    ]


@router.post("/assistant/{suggestion_id}/feedback")
def feedback(
    suggestion_id: PositiveId,
    body: s.Feedback,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> s.DraftView:
    return assistant.feedback(session, user, suggestion_id, body, request.state.request_id)


@router.post("/assistant/{suggestion_id}/send")
def send(
    suggestion_id: PositiveId,
    body: s.SendDraft,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> s.DraftView:
    return assistant.send(session, user, suggestion_id, body, request.state.request_id)


@router.get("/admin/policies")
def policies(user: CurrentUser, session: SessionDep) -> list[dict]:
    tickets.require_role(user, "ADMIN")
    return [
        {
            "id": str(r.id),
            "priority": r.priority,
            "version": r.version,
            "active": r.active,
            "response_seconds": r.response_seconds,
            "resolution_seconds": r.resolution_seconds,
            "warning_seconds": r.warning_seconds,
        }
        for r in session.scalars(
            select(SLAPolicy).order_by(SLAPolicy.priority, SLAPolicy.version.desc()).limit(100)
        )
    ]


@router.post("/admin/policies", status_code=201)
def create_policy(
    body: s.PolicyCreate, user: CurrentUser, session: SessionDep, request: Request
) -> dict:
    return operations.policy(session, user, body, request.state.request_id)


@router.post("/admin/sla-scan")
def scan(user: CurrentUser, session: SessionDep) -> dict:
    tickets.require_role(user, "ADMIN")
    return operations.scan_sla(session)


@router.get("/admin/alerts")
def alerts(
    user: CurrentUser, session: SessionDep, page: Annotated[int, Query(ge=1, le=10000)] = 1
) -> list[dict]:
    tickets.require_role(user, "ADMIN")
    rows = session.execute(
        select(SLAAlert, TicketSLACycle, Ticket)
        .join(TicketSLACycle, SLAAlert.cycle_id == TicketSLACycle.id)
        .join(Ticket, Ticket.id == TicketSLACycle.ticket_id)
        .order_by(SLAAlert.id.desc())
        .offset((page - 1) * 50)
        .limit(50)
    )
    return [
        {
            "id": str(a.id),
            "ticket_id": str(t.id),
            "ticket_no": t.ticket_no,
            "ticket_version": t.version,
            "kind": a.kind,
            "due_at": a.due_at,
            "acknowledged_at": a.acknowledged_at,
            "created_at": a.created_at,
        }
        for a, c, t in rows
    ]


@router.post("/admin/alerts/{alert_id}/acknowledge")
def acknowledge(
    alert_id: PositiveId, user: CurrentUser, session: SessionDep, request: Request
) -> dict:
    tickets.require_role(user, "ADMIN")
    row = session.scalar(select(SLAAlert).where(SLAAlert.id == alert_id).with_for_update())
    if row is None:
        raise AppError(404, "ALERT_NOT_FOUND", "提醒不存在")
    if row.acknowledged_at is None:
        row.acknowledged_at, row.acknowledged_by = utcnow(), user.id
        admin.audit(
            session, user, "SLA_ESCALATION_ACK", "sla_alert", row.id, {}, request.state.request_id
        )
    session.commit()
    return {"id": str(row.id), "acknowledged_at": row.acknowledged_at}


@router.get("/metrics")
def metrics(user: CurrentUser, session: SessionDep) -> dict:
    return operations.metrics(session, user)


@router.get("/admin/configuration")
def configuration(user: CurrentUser, session: SessionDep) -> dict:
    tickets.require_role(user, "ADMIN")
    return {
        "users": [
            b.UserView.model_validate(r).model_dump(mode="json")
            for r in session.scalars(select(User).order_by(User.id).limit(200))
        ],
        "agents": [
            {
                "user_id": str(r.user_id),
                "version": r.version,
                "online": r.online,
                "capacity": r.capacity,
                "active_load": r.active_load,
            }
            for r in session.scalars(select(AgentProfile))
        ],
        "groups": [
            {
                "id": str(r.id),
                "code": r.code,
                "name": r.name,
                "enabled": r.enabled,
                "version": r.version,
            }
            for r in session.scalars(select(BusinessGroup))
        ],
        "members": [
            {
                "group_id": str(r.group_id),
                "user_id": str(r.user_id),
                "can_handle": r.can_handle,
                "can_view_business": r.can_view_business,
            }
            for r in session.scalars(select(GroupMember))
        ],
        "rules": [
            {
                "id": str(r.id),
                "category": r.category,
                "group_id": str(r.group_id),
                "rank": r.rank,
                "enabled": r.enabled,
            }
            for r in session.scalars(select(RoutingRule))
        ],
        "jobs": [
            {
                "id": str(r.id),
                "type": r.type,
                "status": r.status,
                "attempts": r.attempts,
                "last_error": r.last_error,
            }
            for r in session.scalars(select(JobTask).order_by(JobTask.id.desc()).limit(50))
        ],
    }


class Toggle(b.Input):
    enabled: bool
    reason: b.Reason


@router.patch("/admin/routing-rules/{rule_id}")
def toggle_rule(
    rule_id: PositiveId, body: Toggle, user: CurrentUser, session: SessionDep, request: Request
) -> dict:
    tickets.require_role(user, "ADMIN")
    tickets.lock_dispatch(session)
    item = session.get(RoutingRule, rule_id)
    if item is None:
        raise AppError(404, "RULE_NOT_FOUND", "规则不存在")
    item.enabled = body.enabled
    admin.audit(
        session,
        user,
        "ROUTING_TOGGLED",
        "routing_rule",
        item.id,
        body.model_dump(),
        request.state.request_id,
    )
    session.commit()
    return {"id": str(item.id), "enabled": item.enabled}


@router.get("/admin/audit")
def audit_log(
    user: CurrentUser, session: SessionDep, after_id: Annotated[int, Query(ge=0)] = 0
) -> list[dict]:
    tickets.require_role(user, "ADMIN")
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "actor_id": str(r.actor_id),
            "target_type": r.target_type,
            "target_id": str(r.target_id),
            "detail": r.detail,
            "created_at": r.created_at,
        }
        for r in session.scalars(
            select(AdminEvent).where(AdminEvent.id > after_id).order_by(AdminEvent.id).limit(100)
        )
    ]


class GroupToggle(Toggle):
    expected_version: b.Version


@router.patch("/admin/groups/{group_id}")
def group_toggle(
    group_id: PositiveId,
    body: GroupToggle,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict:
    tickets.require_role(user, "ADMIN")
    tickets.lock_dispatch(session)
    item = session.get(BusinessGroup, group_id)
    if item is None:
        raise AppError(404, "GROUP_NOT_FOUND", "业务组不存在")
    tickets.check_version(item, body.expected_version)
    item.enabled = body.enabled
    admin.audit(
        session,
        user,
        "GROUP_TOGGLED",
        "group",
        item.id,
        body.model_dump(),
        request.state.request_id,
    )
    session.commit()
    return {"id": str(item.id), "enabled": item.enabled, "version": item.version}


class MemberUpdate(b.Input):
    can_handle: bool
    can_view_business: bool
    reason: b.Reason


@router.patch("/admin/groups/{group_id}/members/{user_id}")
def member_update(
    group_id: PositiveId,
    user_id: PositiveId,
    body: MemberUpdate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict:
    tickets.require_role(user, "ADMIN")
    tickets.lock_dispatch(session)
    item = session.get(GroupMember, (group_id, user_id))
    if item is None:
        raise AppError(404, "MEMBER_NOT_FOUND", "成员不存在")
    item.can_handle, item.can_view_business = body.can_handle, body.can_view_business
    admin.audit(
        session,
        user,
        "MEMBER_AUTHORIZATION_CHANGED",
        "group",
        group_id,
        {"user_id": str(user_id), **body.model_dump()},
        request.state.request_id,
    )
    session.commit()
    return {
        "group_id": str(group_id),
        "user_id": str(user_id),
        "can_handle": item.can_handle,
        "can_view_business": item.can_view_business,
    }


@router.get("/agents/{user_id}/availability")
def agent_profile(user_id: PositiveId, user: CurrentUser, session: SessionDep) -> b.AgentView:
    tickets.require_role(user, "ADMIN", "AGENT")
    if user.role != "ADMIN" and user.id != user_id:
        raise AppError(403, "FORBIDDEN", "只能读取自己的在线设置")
    profile = session.get(AgentProfile, user_id)
    if profile is None:
        raise AppError(404, "AGENT_NOT_FOUND", "客服不存在")
    return b.AgentView.model_validate(profile, from_attributes=True)


class ClassifyText(b.Input):
    text: Annotated[str, Body(min_length=6, max_length=4000)]


@router.post("/nlp/classify")
def classify_text(
    body: ClassifyText, user: CurrentUser, session: SessionDep, request: Request
) -> dict:
    from backend.app.services.reviews import verify_managed_release
    from nlp.inference.classifier import ArtifactClassifier, ClassifierNotReady

    tickets.require_role(user, "ADMIN", "AGENT")
    path = request.app.state.settings.classifier_manifest
    if not path:
        raise AppError(503, "CLASSIFIER_NOT_APPROVED", "没有通过正式部署审批的分类器，请走人工分类")
    try:
        tickets.lock_dispatch(session)
        verify_managed_release(session, request.app.state.settings, path)
        return ArtifactClassifier(path).predict(body.text)
    except (ClassifierNotReady, OSError, ValueError, KeyError) as exc:
        raise AppError(503, "CLASSIFIER_UNAVAILABLE", "模型校验或推理失败，请走人工分类") from exc


@router.get("/admin/model-reports")
def model_reports(user: CurrentUser, session: SessionDep, request: Request) -> dict:
    import json
    from pathlib import Path

    tickets.require_role(user, "ADMIN")
    root = Path(__file__).resolve().parents[3] / "artifacts"
    target = root / "evaluation/retrieval-demo.json"
    if (root / "evaluation/retrieval-ai-v2.json").is_file():
        target = root / "evaluation/retrieval-ai-v2.json"
    retrieval = None
    if target.is_file():
        report = json.loads(target.read_text(encoding="utf-8"))
        retrieval = {
            "dataset": report["dataset"],
            "review": report.get("review"),
            "deployment_approved": report["deployment_approved"],
            "strategies": [
                {
                    "strategy": name,
                    **{key: value for key, value in values.items() if key != "results"},
                }
                for name, values in report["strategies"].items()
            ],
        }
    from backend.app.services.reviews import deployment_status

    deployment = deployment_status(session, request.app.state.settings)
    classification_path = root / "evaluation/classification-formal.json"
    if not classification_path.is_file():
        classification_path = root / "evaluation/classification-smoke.json"
    return {
        "retrieval": retrieval,
        "classification": json.loads(classification_path.read_text(encoding="utf-8"))
        if classification_path.is_file()
        else None,
        "classifier_deployment_approved": deployment in {"shadow", "live"},
        "classifier_deployment": deployment,
        "classification_note": (
            "AI-reviewed fictional-project offline results are distinct from human gold standards "
            "and production accuracy; deployment requires separate approval"
        ),
    }
