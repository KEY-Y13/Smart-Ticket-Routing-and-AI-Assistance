from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ComponentHealth(BaseModel):
    name: Literal["database", "worker", "retrieval", "index"]
    status: Literal["healthy", "degraded", "unknown"]
    detail: str
    age_seconds: float | None = None


class QueueHealth(BaseModel):
    queued: int
    running: int
    failed: int
    expired_leases: int
    oldest_due_seconds: float | None


class BudgetHealth(BaseModel):
    enabled: bool
    reserved: int
    total: int
    remaining: int


class HTTPHealth(BaseModel):
    uptime_seconds: float
    requests: int
    server_errors: int
    status_counts: dict[str, int]
    latency_samples: int
    p95_ms: float | None
    scope: str


class OperationalHealth(BaseModel):
    generated_at: datetime
    status: Literal["healthy", "degraded"]
    components: list[ComponentHealth]
    queue: QueueHealth
    llm_budget: BudgetHealth
    http: HTTPHealth
    alerts: list[str]
