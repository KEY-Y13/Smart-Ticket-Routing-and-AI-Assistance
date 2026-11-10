import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.api.intelligence import router as intelligence_router
from backend.app.api.monitoring import router as monitoring_router
from backend.app.api.reviews import router as reviews_router
from backend.app.api.routes import router
from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.core.observability import RequestMetrics
from backend.app.core.security import LoginLimiter
from backend.app.db.models import Base, DispatchMutex, SLAPolicy
from backend.app.db.runtime import Database
from backend.app.schemas.system import Capabilities, ErrorResponse, Health

logger = logging.getLogger("smartticket.requests")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(application):
        yield
        database.dispose()

    application = FastAPI(
        title="SmartTicket AI",
        version="1.0.0",
        description=(
            "工单、检索与人工审核后端。业务接口需要专用数据库和 Bearer 登录；付费模型默认禁用。"
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.env != "production" else None,
    )
    application.state.settings = settings
    application.state.database = database
    application.state.login_limiter = LoginLimiter()
    application.state.http_metrics = RequestMetrics()
    application.include_router(router)
    application.include_router(intelligence_router)
    application.include_router(reviews_router)
    application.include_router(monitoring_router)

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        started = perf_counter()
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid4().hex
        )
        length = request.headers.get("content-length")
        if request.method in {"POST", "PATCH", "PUT"} and (
            length is None or not length.isdigit() or int(length) > 400000
        ):
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "BODY_TOO_LARGE",
                        "message": "请求体需要明确长度且不超过400KB",
                        "request_id": request.state.request_id,
                    }
                },
            )
        else:
            try:
                response = await call_next(request)
            except Exception as exc:
                # Do not log input bodies, credentials, arbitrary paths or exception values.
                logger.error(
                    "request=%s unexpected=%s", request.state.request_id, type(exc).__name__
                )
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "服务内部错误",
                            "request_id": request.state.request_id,
                        }
                    },
                )
        response.headers["X-Request-ID"] = request.state.request_id
        application.state.http_metrics.record(response.status_code, perf_counter() - started)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'"
        )
        if settings.env != "production" and request.url.path == "/docs":
            # FastAPI's development Swagger page loads these documented CDN assets.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "connect-src 'self'; frame-ancestors 'none'"
            )
        logger.info(
            "request=%s method=%s status=%s",
            request.state.request_id,
            request.method,
            response.status_code,
        )
        return response

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": "HTTP_ERROR", "message": str(exc.detail)}
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    **detail,
                    "request_id": request.state.request_id,
                }
            },
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求格式或字段不符合要求",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(AppError)
    async def business_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                }
            },
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )

    @application.exception_handler(StaleDataError)
    async def stale_error(request: Request, exc: StaleDataError):
        return await business_error(
            request, AppError(409, "VERSION_CONFLICT", "记录已变化，请刷新")
        )

    @application.exception_handler(IntegrityError)
    async def constraint_error(request: Request, exc: IntegrityError):
        return await business_error(
            request, AppError(409, "CONSTRAINT_CONFLICT", "请求与数据约束冲突")
        )

    @application.exception_handler(OperationalError)
    async def connection_error(request: Request, exc: OperationalError):
        return await business_error(
            request, AppError(503, "DATABASE_UNAVAILABLE", "数据库暂时不可用")
        )

    @application.get("/health/live", tags=["system"])
    def live() -> Health:
        return Health(status="alive", milestone="core_backend")

    @application.get("/health/ready", tags=["system"], responses={503: {"model": ErrorResponse}})
    def ready() -> Health:
        if database.sessions is None:
            raise AppError(503, "DATABASE_NOT_CONFIGURED", "请先审查迁移 SQL 并配置专用业务库")
        try:
            with database.sessions() as session:
                tables = set(inspect(database.engine).get_table_names())
                if not set(Base.metadata.tables).issubset(tables):
                    raise AppError(503, "SCHEMA_NOT_READY", "业务表结构尚未完整迁移")
                priorities = set(
                    session.scalars(select(SLAPolicy.priority).where(SLAPolicy.active.is_(True)))
                )
                if session.get(DispatchMutex, 1) is None or priorities != {0, 1, 2, 3}:
                    raise AppError(503, "DATABASE_NOT_INITIALIZED", "请显式初始化业务配置")
        except SQLAlchemyError as exc:
            raise AppError(503, "DATABASE_UNAVAILABLE", "数据库检查失败") from exc
        return Health(
            status="alive", milestone="reference_backend_ready; model deployments guarded"
        )

    @application.get("/api/v1/capabilities", tags=["system"])
    def capabilities() -> Capabilities:
        from backend.app.services.reviews import deployment_status

        deployment = "not_enabled"
        if database.sessions and settings.classifier_manifest:
            with database.sessions() as session:
                deployment = deployment_status(session, settings)
        return Capabilities(
            milestone="core_backend",
            authentication="configured" if database.sessions else "implemented_unconfigured",
            ticket_persistence="configured" if database.sessions else "implemented_unconfigured",
            classifier_deployment=deployment,
            llm="enabled" if settings.llm_enabled else "disabled",
            readiness="requires_database_verification"
            if database.sessions
            else "requires_database_configuration",
        )

    frontend = Path(__file__).resolve().parents[2] / "frontend/dist"
    if frontend.is_dir():
        application.mount("/", StaticFiles(directory=frontend, html=True), name="web")
    return application


app = create_app()
