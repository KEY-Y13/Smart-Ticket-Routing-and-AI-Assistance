from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.schemas.business import Input, Reason, ResourceId, Version


class KnowledgeCreate(Input):
    document_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=100000)
    source: str = Field(min_length=1, max_length=200)
    valid_from: datetime
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        if self.valid_from.tzinfo is None or (self.valid_until and self.valid_until.tzinfo is None):
            raise ValueError("有效期必须带时区")
        if self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("失效时间必须晚于生效时间")
        return self


class KnowledgeAction(Input):
    expected_version: Version
    action: Literal["review", "publish", "retire"]
    reason: Reason


class KnowledgeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    document_key: str
    revision: int
    title: str
    content: str
    content_hash: str
    source: str
    status: str
    valid_from: datetime
    valid_until: datetime | None
    version: int
    review_note: str | None


class Search(Input):
    query: str = Field(min_length=2, max_length=1000)
    strategy: Literal["bm25", "vector", "hybrid", "rerank"] = "rerank"
    top_k: int = Field(default=5, ge=1, le=10)


class Evidence(BaseModel):
    chunk_id: ResourceId
    document_id: ResourceId
    title: str
    revision: int
    text: str
    text_hash: str
    source: str
    score: float
    rerank_score: float | None = None


class SearchResult(BaseModel):
    generation: str | None
    strategy: str
    items: list[Evidence]
    latency_ms: int
    sufficient: bool
    reason: str | None


class DraftRequest(Input):
    expected_version: Version
    mode: Literal["evidence", "llm"] = "evidence"


class DraftView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    ticket_id: ResourceId
    version: int
    status: str
    input_version: int
    body: str
    raw_output: dict
    evidence: list[Evidence]
    facts: dict
    model_version: str
    prompt_version: str
    latency_ms: int
    usage: dict
    sent_message_id: ResourceId | None
    final_body: str | None = None


class Feedback(Input):
    expected_version: Version
    decision: Literal["ACCEPTED", "EDITED", "REJECTED"]
    final_body: str | None = Field(default=None, min_length=1, max_length=4000)
    reason: Reason
    knowledge_error: bool = False


class SendDraft(Input):
    expected_version: Version
    ticket_version: Version


class Quote(Input):
    chunk_id: str = Field(pattern=r"^[0-9]+$")
    quote: str = Field(min_length=6, max_length=600)


class ExtractedIntent(Input):
    # Model output is limited to spans of supplied customer text, not invented facts.
    primary_intent: str | None = Field(default=None, max_length=500)
    product: str | None = Field(default=None, max_length=200)
    time: str | None = Field(default=None, max_length=100)
    multiple_intents: bool = False
    missing_information: list[Literal["订单编号", "故障现象", "申请诉求", "时间信息"]] = Field(
        default_factory=list, max_length=4
    )


class LLMOutput(Input):
    intent: ExtractedIntent
    quotes: list[Quote] = Field(max_length=3)
    needs_human_confirmation: Literal[True]


class PolicyCreate(Input):
    priority: Annotated[int, Field(ge=0, le=3)]
    response_seconds: int = Field(ge=60, le=2592000)
    resolution_seconds: int = Field(ge=60, le=2592000)
    warning_seconds: int = Field(ge=0, le=86400)
    reason: Reason

    @model_validator(mode="after")
    def times(self):
        if not 0 <= self.warning_seconds < self.response_seconds <= self.resolution_seconds:
            raise ValueError("SLA时限不合法")
        return self
