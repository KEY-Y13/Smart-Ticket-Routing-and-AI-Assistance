from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.business import Input, Reason, ResourceId, Version


class CollectionCreate(Input):
    code: str = Field(pattern=r"^[a-z0-9_-]{1,100}$")
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["CLASSIFICATION", "KNOWLEDGE", "RETRIEVAL", "LLM"]
    provenance: str = Field(min_length=5, max_length=2000)
    items: list[dict] = Field(min_length=1, max_length=3000)


class CollectionView(BaseModel):
    id: ResourceId
    code: str
    title: str
    kind: str
    provenance: str
    source_sha256: str
    counts: dict[str, int]


class DecisionCreate(Input):
    expected_version: Version
    decision: Literal["APPROVED", "REJECTED"]
    annotation: dict = Field(default_factory=dict)
    reason: str = Field(min_length=5, max_length=1000)
    personally_reviewed: Literal[True]


class DecisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    item_id: ResourceId
    item_version: int
    content_hash: str
    reviewer_id: ResourceId
    reviewer_login: str | None = None
    review_method: Literal["AI", "HUMAN"] = "HUMAN"
    decision: str
    annotation: dict
    reason: str
    created_at: datetime


class ItemView(BaseModel):
    id: ResourceId
    collection_id: ResourceId
    kind: str
    item_key: str
    payload: dict
    content_hash: str
    version: int
    status: str
    latest_decision: DecisionView | None = None


class ItemPage(BaseModel):
    items: list[ItemView]
    total: int
    page: int
    page_size: int


class Freeze(Input):
    reason: Reason
    acknowledged: Literal[True]


class SnapshotView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    collection_id: ResourceId
    created_by: ResourceId
    source_sha256: str
    snapshot_sha256: str
    created_at: datetime
    row_count: int
    reason: str


class ReleaseCreate(Input):
    snapshot_id: ResourceId
    manifest_ref: str = Field(min_length=1, max_length=500)
    scope: Literal["SHADOW", "LIVE"] = "SHADOW"


class ReleaseDecision(Input):
    expected_version: Version
    action: Literal["APPROVE", "REJECT", "REVOKE"]
    reason: str = Field(min_length=5, max_length=1000)
    personally_reviewed: Literal[True]


class ReleaseView(BaseModel):
    id: ResourceId
    snapshot_id: ResourceId
    manifest_ref: str
    scope: str
    status: str
    version: int
    gates: list[dict]
    package_ref: str | None
    reviewed_by: ResourceId | None
    review_reason: str | None
    created_at: datetime
