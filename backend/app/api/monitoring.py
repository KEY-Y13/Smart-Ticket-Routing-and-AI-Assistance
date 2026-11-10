import httpx
from fastapi import APIRouter, Request
from sqlalchemy import func, select, text

from backend.app.api.dependencies import CurrentUser, SessionDep
from backend.app.core.observability import heartbeat_status
from backend.app.db.models import AIInvocation, JobTask, KnowledgeIndex, utcnow
from backend.app.schemas.monitoring import OperationalHealth
from backend.app.services.tickets import require_role

router = APIRouter(prefix="/api/v1/admin/operations", tags=["运行状态"])


def retrieval_status(settings):
    if not settings.retrieval_secret:
        return "unknown", "检索服务未配置认证"
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            result = client.get(
                settings.retrieval_url + "/health",
                headers={
                    "Authorization": "Bearer " + settings.retrieval_secret.get_secret_value(),
                },
            )
            data = result.json()
            if result.status_code == 200 and data.get("alive") is True:
                return "healthy", "检索服务在线" if data.get(
                    "models_loaded"
                ) else "检索服务在线，模型尚未预热"
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return "degraded", "检索服务不可达或认证失败，证据摘录与AI草稿可能不可用"


@router.get("/health")
def health(user: CurrentUser, session: SessionDep, request: Request) -> OperationalHealth:
    require_role(user, "ADMIN")
    settings, now = request.app.state.settings, utcnow()
    session.execute(text("SELECT 1"))
    worker, detail, age = heartbeat_status(
        settings.worker_heartbeat_path, settings.worker_stale_seconds
    )
    retriever, retrieval_detail = retrieval_status(settings)
    indexes = session.scalar(
        select(func.count()).select_from(KnowledgeIndex).where(KnowledgeIndex.active.is_(True))
    )
    components = [
        {"name": "database", "status": "healthy", "detail": "专用业务库查询正常"},
        {"name": "worker", "status": worker, "detail": detail, "age_seconds": age},
        {"name": "retrieval", "status": retriever, "detail": retrieval_detail},
        {
            "name": "index",
            "status": "healthy" if indexes == 1 else "degraded",
            "detail": "存在一个有效索引版本" if indexes == 1 else "有效索引未建立或状态异常",
        },
    ]
    statuses = dict(
        session.execute(select(JobTask.status, func.count()).group_by(JobTask.status)).all()
    )
    oldest = session.scalar(
        select(func.min(JobTask.available_at)).where(
            JobTask.status == "QUEUED",
            JobTask.available_at <= now,
        )
    )
    expired = session.scalar(
        select(func.count())
        .select_from(JobTask)
        .where(
            JobTask.status == "RUNNING",
            JobTask.lease_until < now,
        )
    )
    # Aggregate datetimes can be naive; row TypeDecorators preserve UTC.
    if oldest and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=now.tzinfo)
    oldest_age = max(0, (now - oldest).total_seconds()) if oldest else None
    reserved = session.scalar(select(func.count()).select_from(AIInvocation))
    alerts = [component["detail"] for component in components if component["status"] != "healthy"]
    if expired:
        alerts.append("存在过期任务租约，等待后台恢复处理")
    if oldest_age is not None and oldest_age > settings.queue_warning_seconds:
        alerts.append("任务等待时间超过配置阈值")
    if statuses.get("FAILED", 0):
        alerts.append("存在失败任务，请查看任务审计并人工处理")
    remaining = max(0, settings.llm_call_budget - reserved)
    if settings.llm_enabled and remaining <= max(1, settings.llm_call_budget // 10):
        alerts.append("百炼调用剩余额度接近上限，失败调用也计入额度")
    return OperationalHealth(
        generated_at=now,
        status="degraded" if alerts else "healthy",
        components=components,
        queue={
            "queued": statuses.get("QUEUED", 0),
            "running": statuses.get("RUNNING", 0),
            "failed": statuses.get("FAILED", 0),
            "expired_leases": expired,
            "oldest_due_seconds": oldest_age,
        },
        llm_budget={
            "enabled": settings.llm_enabled,
            "reserved": reserved,
            "total": settings.llm_call_budget,
            "remaining": remaining,
        },
        http=request.app.state.http_metrics.snapshot(),
        alerts=alerts,
    )
