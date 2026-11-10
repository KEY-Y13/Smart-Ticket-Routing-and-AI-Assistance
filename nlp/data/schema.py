from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.:-]+$")]


class TicketSample(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sample_id: Identifier
    text: Annotated[str, Field(min_length=6, max_length=4000)]
    label: Annotated[int, Field(ge=0, le=5, strict=True)]
    group_id: Identifier
    source_kind: Literal["synthetic", "manual", "public"]
    source_ref: Annotated[str, Field(min_length=1, max_length=500)]
    source_license: Annotated[str, Field(min_length=1, max_length=200)]
    rights_status: Literal["project_internal", "licensed", "unknown"]
    review_status: Literal["unreviewed", "approved", "rejected"]
    reviewer: str | None = None

    @model_validator(mode="after")
    def approved_requires_reviewer(self) -> "TicketSample":
        if self.review_status == "approved" and not (self.reviewer and self.reviewer.strip()):
            raise ValueError("approved 样本必须记录实际复核人")
        return self
