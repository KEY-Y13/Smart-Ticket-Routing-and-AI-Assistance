from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from sqlalchemy import select

from backend.app.api.dependencies import CurrentUser, IdempotencyHeader, SessionDep
from backend.app.core.errors import AppError
from backend.app.core.security import create_token, verify_password
from backend.app.db.models import TicketEvent, TicketMessage, User
from backend.app.domain.tickets import Role, Status
from backend.app.schemas import business as schemas
from backend.app.services import admin, business, tickets

router = APIRouter(prefix="/api/v1", tags=["business"])
PositiveId = Annotated[int, Path(ge=1, le=2**63 - 1)]


@router.post("/auth/login")
def login(body: schemas.Login, session: SessionDep, request: Request) -> schemas.TokenView:
    request.app.state.login_limiter.check(request.client.host if request.client else "unknown")
    user = session.scalar(select(User).where(User.login == body.login.casefold()))
    valid = verify_password(body.password.get_secret_value(), user.password_hash if user else None)
    if not valid or not user.enabled:
        raise AppError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
    settings = request.app.state.settings
    return schemas.TokenView(
        access_token=create_token(user, settings),
        expires_in=settings.token_minutes * 60,
        user=schemas.UserView.model_validate(user),
    )


@router.get("/me")
def me(user: CurrentUser) -> schemas.UserView:
    return schemas.UserView.model_validate(user)


@router.post("/auth/logout")
def logout(user: CurrentUser, session: SessionDep) -> dict[str, str]:
    user.auth_version += 1
    session.commit()
    return {"status": "all_sessions_revoked"}


@router.post("/tickets", status_code=201)
def create_ticket(
    body: schemas.TicketCreate,
    key: IdempotencyHeader,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.TicketView:
    return tickets.create_ticket(session, user, body, key, request.state.request_id)


@router.get("/tickets")
def list_tickets(
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Status | None = None,
    category: Annotated[int | None, Query(ge=0, le=5)] = None,
    priority: Annotated[int | None, Query(ge=0, le=3)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> schemas.TicketPage:
    return tickets.list_tickets(session, user, page, page_size, status, category, priority, q)


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: PositiveId, user: CurrentUser, session: SessionDep) -> schemas.TicketView:
    return tickets.ticket_view(tickets.get_ticket(session, user, ticket_id))


@router.patch("/tickets/{ticket_id}")
def patch_ticket(
    ticket_id: PositiveId,
    body: schemas.TicketPatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.TicketView:
    return tickets.patch_ticket(session, user, ticket_id, body, request.state.request_id)


@router.post("/tickets/{ticket_id}/transitions")
def transition(
    ticket_id: PositiveId,
    body: schemas.Transition,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.TicketView:
    return tickets.transition_ticket(session, user, ticket_id, body, request.state.request_id)


@router.post("/tickets/{ticket_id}/classification-review")
def review_classification(
    ticket_id: PositiveId,
    body: schemas.ClassificationReview,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.TicketView:
    return tickets.classify_manually(session, user, ticket_id, body, request.state.request_id)


@router.post("/tickets/{ticket_id}/assign")
def assign(
    ticket_id: PositiveId,
    body: schemas.Assign,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.AssignmentResult:
    return tickets.assign_ticket(session, user, ticket_id, body, request.state.request_id)


@router.post("/tickets/{ticket_id}/messages", status_code=201)
def add_message(
    ticket_id: PositiveId,
    body: schemas.MessageCreate,
    key: IdempotencyHeader,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.MessageResult:
    return tickets.add_message(session, user, ticket_id, body, key, request.state.request_id)


@router.get("/tickets/{ticket_id}/messages")
def messages(
    ticket_id: PositiveId,
    user: CurrentUser,
    session: SessionDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> schemas.MessagePage:
    tickets.get_ticket(session, user, ticket_id)
    conditions = [TicketMessage.ticket_id == ticket_id, TicketMessage.id > after_id]
    if user.role == Role.CUSTOMER:
        conditions.append(TicketMessage.visibility == "PUBLIC")
    rows = list(
        session.scalars(
            select(TicketMessage).where(*conditions).order_by(TicketMessage.id).limit(limit)
        )
    )
    return schemas.MessagePage(
        items=[schemas.MessageView.model_validate(row) for row in rows],
        next_cursor=rows[-1].id if len(rows) == limit else None,
    )


@router.get("/tickets/{ticket_id}/events")
def events(
    ticket_id: PositiveId,
    user: CurrentUser,
    session: SessionDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[schemas.EventView]:
    tickets.get_ticket(session, user, ticket_id)
    query = select(TicketEvent).where(TicketEvent.ticket_id == ticket_id, TicketEvent.id > after_id)
    if user.role == Role.CUSTOMER:
        query = query.where(TicketEvent.event_type.in_(["CREATED", "STATUS_CHANGED"]))
    result = []
    for row in session.scalars(query.order_by(TicketEvent.id).limit(limit)):
        item = schemas.EventView.model_validate(row)
        if user.role == Role.CUSTOMER:
            item.reason = "工单进度已更新"
            item.old_value = {k: v for k, v in item.old_value.items() if k == "status"}
            item.new_value = {k: v for k, v in item.new_value.items() if k == "status"}
        result.append(item)
    return result


@router.get("/tickets/{ticket_id}/sla")
def sla(ticket_id: PositiveId, user: CurrentUser, session: SessionDep) -> schemas.SLAView:
    ticket = tickets.get_ticket(session, user, ticket_id)
    return schemas.SLAView.model_validate(tickets.active_cycle(session, ticket))


@router.get("/tickets/{ticket_id}/business-context")
def context(
    ticket_id: PositiveId, user: CurrentUser, session: SessionDep, request: Request
) -> schemas.BusinessContext:
    return schemas.BusinessContext.model_validate(
        business.business_context(session, user, ticket_id, request.state.request_id)
    )


@router.post("/admin/users", status_code=201)
def create_user(
    body: schemas.UserCreate, user: CurrentUser, session: SessionDep, request: Request
) -> schemas.UserView:
    return admin.create_user(session, user, body, request.state.request_id)


@router.patch("/admin/users/{user_id}")
def patch_user(
    user_id: PositiveId,
    body: schemas.UserPatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.UserView:
    return admin.patch_user(session, user, user_id, body, request.state.request_id)


@router.post("/admin/groups", status_code=201)
def create_group(
    body: schemas.GroupCreate, user: CurrentUser, session: SessionDep, request: Request
) -> schemas.GroupView:
    return schemas.GroupView.model_validate(
        admin.create_group(session, user, body, request.state.request_id)
    )


@router.post("/admin/groups/{group_id}/members", status_code=201)
def add_member(
    group_id: PositiveId,
    body: schemas.MemberCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.MemberView:
    return schemas.MemberView.model_validate(
        admin.add_member(session, user, group_id, body, request.state.request_id)
    )


@router.post("/admin/routing-rules", status_code=201)
def create_rule(
    body: schemas.RuleCreate, user: CurrentUser, session: SessionDep, request: Request
) -> schemas.RuleView:
    return schemas.RuleView.model_validate(
        admin.create_rule(session, user, body, request.state.request_id)
    )


@router.post("/admin/dispatch-next")
def dispatch_next(
    body: schemas.DispatchNext, user: CurrentUser, session: SessionDep, request: Request
) -> schemas.DispatchNextResult:
    return tickets.dispatch_next(session, user, body, request.state.request_id)


@router.patch("/agents/{user_id}/availability")
def agent_availability(
    user_id: PositiveId,
    body: schemas.AgentUpdate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> schemas.AgentView:
    return schemas.AgentView.model_validate(
        admin.update_agent(session, user, user_id, body, request.state.request_id)
    )
