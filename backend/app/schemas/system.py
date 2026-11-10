from typing import Literal

from pydantic import BaseModel, ConfigDict


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["alive"]
    milestone: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Capabilities(BaseModel):
    milestone: str
    authentication: Literal["implemented_unconfigured", "configured"]
    ticket_persistence: Literal["implemented_unconfigured", "configured"]
    classifier_deployment: Literal[
        "not_enabled", "configured_requires_validation", "invalid", "shadow", "live"
    ]
    llm: Literal["disabled", "enabled"]
    readiness: Literal["requires_database_verification", "requires_database_configuration"]
