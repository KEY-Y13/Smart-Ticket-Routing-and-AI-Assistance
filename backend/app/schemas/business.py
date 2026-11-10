from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, SecretStr

from backend.app.domain.tickets import Role, Status

ResourceId = Annotated[
    int, Field(gt=0, le=2**63 - 1), PlainSerializer(str, return_type=str, when_used="json")
]
Version = Annotated[int, Field(ge=1, strict=True)]
Reason = Annotated[str, Field(min_length=1, max_length=1000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    login: Annotated[str, Field(min_length=1, max_length=100)]
    password: SecretStr = Field(min_length=1, max_length=128)


class UserCreate(Input):
    login: Annotated[str, Field(min_length=3, max_length=100, pattern=r"^[a-z0-9_.-]+$")]
    password: SecretStr = Field(min_length=12, max_length=128)
    role: Role


class UserPatch(Input):
    expected_version: Version
    enabled: bool | None = None
    role: Role | None = None


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    login: str
    role: Role
    enabled: bool
    version: int


class TokenView(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserView


class TicketCreate(Input):
    subject: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=6, max_length=4000)]
    order_no: Annotated[str, Field(pattern=r"^ST\d{12}$")] | None = None


class TicketPatch(Input):
    expected_version: Version
    subject: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    priority: Annotated[int, Field(ge=0, le=3, strict=True)] | None = None
    reason: Reason


class Transition(Input):
    expected_version: Version
    target_status: Status
    reason: Reason


class ClassificationReview(Input):
    expected_version: Version
    category: Annotated[int, Field(ge=0, le=5, strict=True)]
    reason: Reason


class Assign(Input):
    expected_version: Version
    agent_id: ResourceId | None = None
    reason: Reason


class MessageCreate(Input):
    expected_version: Version
    body: Annotated[str, Field(min_length=1, max_length=4000)]
    visibility: Literal["PUBLIC", "INTERNAL"] = "PUBLIC"


class TicketView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    ticket_no: str
    customer_id: ResourceId
    order_id: ResourceId | None
    subject: str
    category: int | None
    priority: int
    status: Status
    assignee_id: ResourceId | None
    analysis_status: str
    version: int
    created_at: datetime


class TicketPage(BaseModel):
    items: list[TicketView]
    total: int
    page: int
    page_size: int


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    author_id: ResourceId
    visibility: str
    body: str
    created_at: datetime


class MessageResult(BaseModel):
    message: MessageView
    ticket: TicketView


class AssignmentResult(BaseModel):
    ticket: TicketView
    assigned: bool
    queue_reason: str | None


class DispatchNext(Input):
    reason: Reason
    category: Annotated[int, Field(ge=0, le=5, strict=True)] | None = None


class DispatchNextResult(BaseModel):
    queue_state: Literal["NO_CLASSIFIED_TICKET", "ATTEMPTED"]
    assignment: AssignmentResult | None


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: ResourceId
    actor_id: ResourceId | None
    event_type: str
    old_value: dict
    new_value: dict
    reason: str
    created_at: datetime


class GroupCreate(Input):
    code: Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=100)]


class MemberCreate(Input):
    user_id: ResourceId
    can_handle: bool = True
    can_view_business: bool = False


class RuleCreate(Input):
    category: Annotated[int, Field(ge=0, le=5, strict=True)]
    group_id: ResourceId
    rank: Annotated[int, Field(ge=0, le=100, strict=True)] = 0


class AgentUpdate(Input):
    expected_version: Version
    online: bool
    capacity: Annotated[int, Field(ge=1, le=100, strict=True)] | None = None


class MessagePage(BaseModel):
    items: list[MessageView]
    next_cursor: ResourceId | None


class BusinessOrder(BaseModel):
    id: ResourceId
    order_no: str
    status: str
    total_amount: str


class BusinessItem(BaseModel):
    product_name: str
    quantity: int
    unit_price: str


class BusinessPayment(BaseModel):
    status: str
    amount: str
    occurred_at: datetime


class BusinessShipment(BaseModel):
    carrier: str
    tracking_no: str
    status: str


class BusinessRefund(BaseModel):
    status: str
    amount: str


class BusinessContext(BaseModel):
    source: Literal["SIMULATED_BUSINESS_DATABASE"]
    queried_at: datetime
    order: BusinessOrder
    items: list[BusinessItem]
    payments: list[BusinessPayment]
    shipments: list[BusinessShipment]
    refunds: list[BusinessRefund]


class GroupView(BaseModel):
    id: ResourceId
    code: str
    name: str


class MemberView(BaseModel):
    group_id: ResourceId
    user_id: ResourceId


class RuleView(BaseModel):
    id: ResourceId
    category: int
    group_id: ResourceId


class AgentView(BaseModel):
    user_id: ResourceId
    online: bool
    capacity: int
    active_load: int
    version: int


class SLAView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    cycle_no: int
    started_at: datetime
    response_due_at: datetime
    resolution_due_at: datetime
    first_response_at: datetime | None
    resolved_at: datetime | None
