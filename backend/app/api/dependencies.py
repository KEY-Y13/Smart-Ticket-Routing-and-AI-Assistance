from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError
from backend.app.core.security import decode_token
from backend.app.db.models import User

bearer = HTTPBearer(auto_error=False)


def get_session(request: Request):
    factory = request.app.state.database.sessions
    if factory is None:
        raise AppError(503, "DATABASE_NOT_CONFIGURED", "业务数据库尚未配置或迁移")
    with factory() as session:
        try:
            yield session
        finally:
            session.rollback()  # writes commit explicitly; failed operations leave no partial data


SessionDep = Annotated[Session, Depends(get_session)]


def current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise AppError(401, "AUTHENTICATION_REQUIRED", "请先登录")
    claims = decode_token(credentials.credentials, request.app.state.settings)
    user = session.get(User, int(claims["sub"]))
    if user is None or not user.enabled or user.auth_version != claims["ver"]:
        raise AppError(401, "INVALID_TOKEN", "登录已失效，请重新登录")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
IdempotencyHeader = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
