from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator

from .enums import ConversationState, MessageStatus, NoteVisibility, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from .utils.time import format_utc



class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('*', when_used='json', check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUserRead(APIModel):

    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    role: UserRole
    team_id: Optional[int] = None
    capabilities: list[str] = Field(default_factory=list)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserRead


class TeamRead(APIModel):

    id: int
    name: str
    team_type: str
    market_id: Optional[int] = None


class UserRead(APIModel):

    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    role: UserRole
    team_id: Optional[int] = None
    is_active: bool = True
    capabilities: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    team_id: Optional[int] = None
    capabilities: Optional[list[str]] = None

class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=6)

class ExternalChannelUnresolvedEventRead(APIModel):
    id: int
    source: str
    session_key: Optional[str] = None
    event_type: Optional[str] = None
    recipient: Optional[str] = None
    source_chat_id: Optional[str] = None
    preferred_reply_contact: Optional[str] = None
    status: str
    replay_count: int
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TagRead(APIModel):

    id: int
    name: str
    color: Optional[str] = None


class CustomerInput(BaseModel):
    id: Optional[int] = None
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    external_ref: Optional[str] = None


class CustomerRead(APIModel):

    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    external_ref: Optional[str] = None


class TicketCreate(BaseModel):
    title: str
    description: str
    source: TicketSource
    source_channel: SourceChannel
    priority: TicketPriority
    category: Optional[str] = None
    sub_category: Optional[str] = None
    tracking_number: Optional[str] = None
    team_id: Optional[int] = None
    assignee_id: Optional[int] = None
    market_id: Optional[int] = None
    market_code: Optional[str] = None
    country_code: Optional[str] = None
    conversation_state: Optional[ConversationState] = None
    customer: Optional[CustomerInput] = None
    customer_id: Optional[int] = None
    tag_ids: list[int] = Field(default_factory=list)
    ai_summary: Optional[str] = None
    ai_classification: Optional[str] = None
    ai_confidence: Optional[float] = None
    case_type: Optional[str] = None
    issue_summary: Optional[str] = None
    customer_request: Optional[str] = None
    source_chat_id: Optional[str] = None
    required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    last_customer_message: Optional[str] = None
    customer_update: Optional[str] = None
    resolution_summary: Optional[str] = None
    last_human_update: Optional[str] = None
    requested_time: Optional[str] = None
    destination: Optional[str] = None
    preferred_reply_channel: Optional[str] = None
    preferred_reply_contact: Optional[str] = None


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[TicketPriority] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    resolution_category: Optional[ResolutionCategory] = None
    market_id: Optional[int] = None
    country_code: Optional[str] = None
    tag_ids: Optional[list[int]] = None
    case_type: Optional[str] = None
    issue_summary: Optional[str] = None
    customer_request: Optional[str] = None
    source_chat_id: Optional[str] = None
    required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    last_customer_message: Optional[str] = None
    customer_update: Optional[str] = None
    resolution_summary: Optional[str] = None
    last_human_update: Optional[str] = None
    requested_time: Optional[str] = None
    destination: Optional[str] = None
    preferred_reply_channel: Optional[str] = None
    preferred_reply_contact: Optional[str] = None


class TicketAssignRequest(BaseModel):
    assignee_id: Optional[int] = None
    team_id: Optional[int] = None
    note: Optional[str] = None


class TicketStatusChangeRequest(BaseModel):
    new_status: TicketStatus
    note: Optional[str] = None


class TicketEscalateRequest(BaseModel):
    team_id: int
    note: str


class TicketReopenRequest(BaseModel):
    reason: str
    assign_to_previous: bool = True
    restore_team: bool = True


class CommentCreate(BaseModel):
    body: str
    visibility: NoteVisibility = NoteVisibility.external


class InternalNoteCreate(BaseModel):
    body: str


class AIIntakeCreate(BaseModel):
    summary: str
    classification: Optional[str] = None
    confidence: Optional[float] = None
    missing_fields: list[str] = Field(default_factory=list)
    recommended_action: Optional[str] = None
    suggested_reply: Optional[str] = None
    raw_payload: Optional[dict[str, Any]] = None
    human_override_reason: Optional[str] = None
    market_id: Optional[int] = None
    country_code: Optional[str] = None


class OutboundDraftCreate(BaseModel):
    channel: SourceChannel
    subject: Optional[str] = Field(default=None, max_length=255)
    body: str
    attachment_ids: list[int] = Field(default_factory=list)


class OutboundSendRequest(BaseModel):
    channel: SourceChannel
    subject: Optional[str] = Field(default=None, max_length=255)
    body: str
    attachment_ids: list[int] = Field(default_factory=list)


class InboundEmailIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_address: str = Field(min_length=3, max_length=320)
    from_name: Optional[str] = Field(default=None, max_length=160)
    to_address: Optional[str] = Field(default=None, max_length=320)
    cc: Optional[str] = Field(default=None, max_length=2000)
    subject: Optional[str] = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=40000)
    provider: str = Field(default="manual", min_length=1, max_length=80)
    provider_message_id: Optional[str] = Field(default=None, max_length=255)
    mailbox_thread_id: Optional[str] = Field(default=None, max_length=255)
    mailbox_message_id: Optional[str] = Field(default=None, max_length=255)
    mailbox_references: Optional[str] = Field(default=None, max_length=4000)
    in_reply_to: Optional[str] = Field(default=None, max_length=255)
    received_at: Optional[datetime] = None

    @field_validator(
        "from_address",
        "from_name",
        "to_address",
        "cc",
        "subject",
        "body",
        "provider",
        "provider_message_id",
        "mailbox_thread_id",
        "mailbox_message_id",
        "mailbox_references",
        "in_reply_to",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class AttachmentRead(APIModel):

    id: int
    file_name: str
    download_url: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    visibility: NoteVisibility
    created_at: datetime


class CommentRead(APIModel):

    id: int
    ticket_id: int
    author_id: Optional[int] = None
    body: str
    visibility: NoteVisibility
    created_at: datetime


class InternalNoteRead(APIModel):

    id: int
    ticket_id: int
    author_id: Optional[int] = None
    body: str
    created_at: datetime


class OutboundMessageRead(APIModel):

    id: int
    ticket_id: int
    channel: SourceChannel
    status: MessageStatus
    subject: Optional[str] = None
    body: str
    provider_status: Optional[str] = None
    provider_message_id: Optional[str] = None
    mailbox_thread_id: Optional[str] = None
    mailbox_message_id: Optional[str] = None
    mailbox_references: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 0
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None
    delivery_status: Optional[str] = None
    delivery_event_type: Optional[str] = None
    delivery_receipt_provider: Optional[str] = None
    delivery_receipt_id: Optional[str] = None
    delivery_receipt_at: Optional[datetime] = None
    delivery_detail: Optional[str] = None
    sent_at: Optional[datetime] = None
    created_at: datetime
    attachments: list[AttachmentRead] = Field(default_factory=list)


class InboundEmailMessageRead(APIModel):
    id: int
    ticket_id: int
    actor_id: Optional[int] = None
    source: str
    provider: str
    provider_message_id: Optional[str] = None
    from_address: str
    from_name: Optional[str] = None
    to_address: Optional[str] = None
    cc: Optional[str] = None
    subject: Optional[str] = None
    body: str
    body_preview: Optional[str] = None
    mailbox_thread_id: str
    mailbox_message_id: Optional[str] = None
    mailbox_references: Optional[str] = None
    in_reply_to: Optional[str] = None
    ticket_event_id: Optional[int] = None
    audit_id: Optional[int] = None
    received_at: datetime
    created_at: datetime


class InboundEmailIngestResponse(APIModel):
    ok: bool
    created: bool
    message: InboundEmailMessageRead
    ticket_event_id: Optional[int] = None
    audit_id: Optional[int] = None


class EmailDeliveryReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_status: Literal["accepted", "delivered", "opened", "deferred", "bounced", "failed", "rejected", "complained"]
    provider: str = Field(default="manual", min_length=1, max_length=80)
    provider_event_type: Optional[str] = Field(default=None, max_length=80)
    provider_event_id: Optional[str] = Field(default=None, max_length=255)
    provider_status: Optional[str] = Field(default=None, max_length=120)
    provider_message_id: Optional[str] = Field(default=None, max_length=255)
    mailbox_message_id: Optional[str] = Field(default=None, max_length=255)
    detail: Optional[str] = Field(default=None, max_length=2000)
    failure_code: Optional[str] = Field(default=None, max_length=120)
    failure_reason: Optional[str] = Field(default=None, max_length=2000)
    occurred_at: Optional[datetime] = None
    raw_payload: Optional[dict[str, Any]] = None

    @field_validator(
        "delivery_status",
        "provider",
        "provider_event_type",
        "provider_event_id",
        "provider_status",
        "provider_message_id",
        "mailbox_message_id",
        "detail",
        "failure_code",
        "failure_reason",
        mode="before",
    )
    @classmethod
    def strip_receipt_strings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class EmailDeliveryReceiptResponse(APIModel):
    ok: bool
    created: bool
    message_id: int
    ticket_id: int
    status: MessageStatus
    provider_status: Optional[str] = None
    delivery_status: str
    delivery_event_type: Optional[str] = None
    delivery_receipt_provider: Optional[str] = None
    delivery_receipt_id: Optional[str] = None
    delivery_receipt_at: Optional[datetime] = None
    delivery_detail: Optional[str] = None
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None
    ticket_event_id: Optional[int] = None
    audit_id: Optional[int] = None


class EmailMailboxQueueItem(APIModel):
    id: int
    ticket_id: int
    ticket_no: Optional[str] = None
    title: str
    status: str
    priority: str
    source_channel: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    tracking_number: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    assignee_name: Optional[str] = None
    team_name: Optional[str] = None
    market_id: Optional[int] = None
    market_code: Optional[str] = None
    country_code: Optional[str] = None
    conversation_state: Optional[str] = None
    updated_at: datetime
    resolution_due_at: Optional[datetime] = None
    overdue: bool = False
    queue_source: Literal["inbound_email", "outbound_message", "ticket_marker"]
    queue_reason: str
    direction: Literal["inbound", "outbound", "ticket"]
    last_message_at: Optional[datetime] = None
    last_message_subject: Optional[str] = None
    last_message_preview: Optional[str] = None
    mailbox_thread_id: Optional[str] = None
    mailbox_message_id: Optional[str] = None
    mailbox_references: Optional[str] = None
    provider: Optional[str] = None
    provider_status: Optional[str] = None
    delivery_status: Optional[str] = None
    outbound_message_id: Optional[int] = None
    inbound_message_id: Optional[int] = None


class EmailMailboxQueueResponse(APIModel):
    generated_at: datetime
    source: Literal["mailbox_projection"] = "mailbox_projection"
    items: list[EmailMailboxQueueItem] = Field(default_factory=list)
    total: int
    filters: dict[str, Any] = Field(default_factory=dict)


class AIIntakeRead(APIModel):

    id: int
    ticket_id: int
    summary: str
    classification: Optional[str] = None
    confidence: Optional[float] = None
    recommended_action: Optional[str] = None
    suggested_reply: Optional[str] = None
    human_override_reason: Optional[str] = None
    created_at: datetime


class TicketListItem(APIModel):
    id: int
    ticket_no: str
    title: str
    status: TicketStatus
    priority: TicketPriority
    source_channel: SourceChannel
    category: Optional[str] = None
    sub_category: Optional[str] = None
    tracking_number: Optional[str] = None
    customer_name: Optional[str] = None
    market_code: Optional[str] = None
    country_code: Optional[str] = None
    conversation_state: Optional[ConversationState] = None
    assignee_name: Optional[str] = None
    team_name: Optional[str] = None
    updated_at: datetime
    resolution_due_at: Optional[datetime] = None
    overdue: bool = False


class TicketRead(APIModel):
    id: int
    ticket_no: str
    title: str
    description: str
    source: TicketSource
    source_channel: SourceChannel
    priority: TicketPriority
    status: TicketStatus
    category: Optional[str] = None
    sub_category: Optional[str] = None
    tracking_number: Optional[str] = None
    case_type: Optional[str] = None
    issue_summary: Optional[str] = None
    customer_request: Optional[str] = None
    source_chat_id: Optional[str] = None
    required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    last_customer_message: Optional[str] = None
    customer_update: Optional[str] = None
    resolution_summary: Optional[str] = None
    last_human_update: Optional[str] = None
    requested_time: Optional[str] = None
    destination: Optional[str] = None
    preferred_reply_channel: Optional[str] = None
    preferred_reply_contact: Optional[str] = None
    market_id: Optional[int] = None
    market_code: Optional[str] = None
    country_code: Optional[str] = None
    conversation_state: Optional[ConversationState] = None
    customer: Optional[CustomerRead] = None
    assignee: Optional[UserRead] = None
    team: Optional[TeamRead] = None
    tags: list[TagRead] = Field(default_factory=list)
    ai_summary: Optional[str] = None
    ai_classification: Optional[str] = None
    ai_confidence: Optional[float] = None
    first_response_at: Optional[datetime] = None
    first_response_due_at: Optional[datetime] = None
    resolution_due_at: Optional[datetime] = None
    first_response_breached: bool = False
    resolution_breached: bool = False
    reopen_count: int = 0
    resolution_category: ResolutionCategory
    created_at: datetime
    updated_at: datetime
    comments: list[CommentRead] = Field(default_factory=list)
    internal_notes: list[InternalNoteRead] = Field(default_factory=list)
    attachments: list[AttachmentRead] = Field(default_factory=list)
    outbound_messages: list[OutboundMessageRead] = Field(default_factory=list)
    ai_intakes: list[AIIntakeRead] = Field(default_factory=list)
    external_channel_conversation: Optional[ExternalChannelConversationRead] = None
    external_channel_transcript: list[ExternalChannelTranscriptRead] = Field(default_factory=list)
    external_channel_attachment_references: list["ExternalChannelAttachmentReferenceRead"] = Field(default_factory=list)
    active_market_bulletins: list["MarketBulletinRead"] = Field(default_factory=list)


class LiteCaseListItem(APIModel):
    id: int
    case: str
    case_type: Optional[str] = None
    issue_summary: Optional[str] = None
    status: str
    priority: str
    tracking_number: Optional[str] = None
    customer_contact: Optional[str] = None
    market_id: Optional[int] = None
    country_code: Optional[str] = None
    assigned_to: Optional[str] = None
    last_updated: datetime
    highlighted: bool = False


class LiteCaseDetail(APIModel):
    id: int
    case: str
    case_type: Optional[str] = None
    issue_summary: Optional[str] = None
    customer_request: Optional[str] = None
    status: str
    priority: str
    customer_name: Optional[str] = None
    customer_contact: Optional[str] = None
    tracking_number: Optional[str] = None
    channel: str
    source_chat_id: Optional[str] = None
    assigned_to: Optional[str] = None
    required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    last_customer_message: Optional[str] = None
    customer_update: Optional[str] = None
    resolution_summary: Optional[str] = None
    last_human_update: Optional[str] = None
    created_at: datetime
    last_updated: datetime
    requested_time: Optional[str] = None
    destination: Optional[str] = None
    preferred_reply_channel: Optional[str] = None
    preferred_reply_contact: Optional[str] = None
    ai_summary: Optional[str] = None
    ai_case_type: Optional[str] = None
    ai_suggested_required_action: Optional[str] = None
    ai_missing_fields: Optional[str] = None


class LiteCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_type: Optional[str] = None
    issue_summary: str
    customer_request: str
    priority: Optional[str] = "medium"
    customer_name: Optional[str] = None
    customer_contact: Optional[str] = None
    tracking_number: Optional[str] = None
    channel: Optional[str] = "whatsapp"
    source_chat_id: Optional[str] = None
    assigned_to: Optional[int] = None
    team_id: Optional[int] = None
    required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    last_customer_message: Optional[str] = None
    customer_update: Optional[str] = None
    resolution_summary: Optional[str] = None
    requested_time: Optional[str] = None
    destination: Optional[str] = None
    preferred_reply_channel: Optional[str] = None
    preferred_reply_contact: Optional[str] = None
    ai_summary: Optional[str] = None
    ai_case_type: Optional[str] = None
    ai_suggested_required_action: Optional[str] = None
    ai_missing_fields: Optional[str] = None
    upsert_open_case: bool = True


class LiteCaseUpdate(BaseModel):
    case_type: Optional[str] = None
    issue_summary: Optional[str] = None
    customer_request: Optional[str] = None
    priority: Optional[str] = None
    tracking_number: Optional[str] = None
    required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    last_customer_message: Optional[str] = None
    customer_update: Optional[str] = None
    resolution_summary: Optional[str] = None
    requested_time: Optional[str] = None
    destination: Optional[str] = None
    preferred_reply_channel: Optional[str] = None
    preferred_reply_contact: Optional[str] = None


class LiteAssignRequest(BaseModel):
    assignee_id: Optional[int] = None
    team_id: Optional[int] = None


class LiteStatusRequest(BaseModel):
    status: str


class LiteWorkflowUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    customer_update: Optional[str] = None
    resolution_summary: Optional[str] = None
    assignee_id: Optional[int] = None
    team_id: Optional[int] = None
    status: Optional[str] = None
    human_note: Optional[str] = None


class LiteHumanNoteRequest(BaseModel):
    note: str


class LiteAIIntakeRequest(BaseModel):
    ai_summary: Optional[str] = None
    case_type: Optional[str] = None
    suggested_required_action: Optional[str] = None
    missing_fields: Optional[str] = None
    last_customer_message: Optional[str] = None


class LiteQAAppealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_key: str = Field(min_length=1, max_length=160)
    ticket_id: int = Field(gt=0)
    channel: Optional[str] = Field(default=None, max_length=40)
    sample: Optional[str] = Field(default=None, max_length=200)
    current_score: Optional[int] = Field(default=None, ge=0, le=100)
    requested_score: Optional[int] = Field(default=None, ge=0, le=100)
    reason: str = Field(min_length=4, max_length=2000)
    evidence: list[str] = Field(default_factory=list)

    @field_validator("sample_key", "channel", "sample", "reason", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()][:12]


class LiteQAAppealResponse(APIModel):
    ok: bool
    task_id: int
    created: bool
    status: str
    ticket_id: int
    sample_key: str
    appeal_status: str
    submitted_at: datetime


class LiteQAKnowledgeGapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=4, max_length=200)
    source: Optional[str] = Field(default=None, max_length=120)
    ticket_id: Optional[int] = Field(default=None, gt=0)
    channel: Optional[str] = Field(default=None, max_length=40)
    sample: Optional[str] = Field(default=None, max_length=400)
    summary: Optional[str] = Field(default=None, max_length=2000)
    evidence: list[str] = Field(default_factory=list)

    @field_validator("gap_key", "title", "source", "channel", "sample", "summary", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()][:12]


class LiteQAKnowledgeGapResponse(APIModel):
    ok: bool
    resource_id: int
    resource_key: str
    task_id: int
    created: bool
    status: str
    ticket_id: Optional[int] = None
    gap_key: str
    submitted_at: datetime


class LiteControlTowerActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_key: str = Field(min_length=1, max_length=120)
    label: Optional[str] = Field(default=None, max_length=160)
    href: Optional[str] = Field(default=None, max_length=160)
    count: Optional[int] = Field(default=None, ge=0)
    note: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("action_key", "label", "href", "note", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class LiteControlTowerActionResponse(APIModel):
    ok: bool
    task_id: int
    created: bool
    status: str
    action_key: str
    submitted_at: datetime


class LiteMetaRead(APIModel):
    users: list[UserRead]
    teams: list[TeamRead]
    statuses: list[str]
    priorities: list[str]


class TicketStatsRead(BaseModel):
    total: int
    overdue_count: int
    my_open_count: int
    by_status: dict[str, int]


class TimelineItemRead(APIModel):
    id: str
    kind: str
    title: str
    summary: str
    visibility: str
    actor_id: Optional[int] = None
    actor_display_name: Optional[str] = None
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class CustomerHistoryItem(APIModel):
    ticket_id: int
    ticket_no: str
    title: str
    status: TicketStatus
    priority: TicketPriority
    updated_at: datetime


class CustomerHistoryRead(APIModel):
    customer_id: int
    total_tickets: int
    recent_tickets: list[CustomerHistoryItem]


class CapabilityOverrideUpsertRequest(BaseModel):
    capability: str
    allowed: bool = True


class CapabilityOverrideRead(APIModel):
    id: int
    user_id: int
    capability: str
    allowed: bool
    created_at: datetime
    updated_at: datetime


class UserCapabilityMatrixRead(APIModel):
    user: UserRead
    effective_capabilities: list[str]
    overrides: list[CapabilityOverrideRead]


class SecurityCapabilityUserRead(APIModel):
    user_id: int
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    effective_capabilities: list[str] = Field(default_factory=list)
    override_count: int = 0
    high_risk_count: int = 0


class AdminAuditLogRead(APIModel):
    id: int
    actor_id: Optional[int] = None
    actor_username: Optional[str] = None
    actor_display_name: Optional[str] = None
    action: str
    target_type: str
    target_id: Optional[int] = None
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    created_at: datetime


class SecurityAuditSummaryRead(APIModel):
    total_users: int
    active_users: int
    inactive_users: int
    admin_users: int
    auditor_users: int
    high_risk_overrides: int
    recent_audit_24h: int
    catalog_size: int
    read_only: bool


class SecurityAuditRead(APIModel):
    capability_catalog: list[str] = Field(default_factory=list)
    users: list[SecurityCapabilityUserRead] = Field(default_factory=list)
    recent_audit: list[AdminAuditLogRead] = Field(default_factory=list)
    summary: SecurityAuditSummaryRead


class IntegrationClientRead(APIModel):
    id: int
    name: str
    key_id: str
    scopes_csv: str
    rate_limit_per_minute: int
    is_active: bool
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class MarketCreate(BaseModel):
    code: str
    name: str
    country_code: str
    language_code: Optional[str] = None
    timezone: Optional[str] = None


class MarketRead(APIModel):
    id: int
    code: str
    name: str
    country_code: str
    language_code: Optional[str] = None
    timezone: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TeamMarketAssignRequest(BaseModel):
    market_id: Optional[int] = None


class ExternalChannelLinkRequest(BaseModel):
    ticket_id: int
    session_key: str
    channel: Optional[str] = None
    recipient: Optional[str] = None
    account_id: Optional[str] = None
    thread_id: Optional[str] = None
    route: Optional[dict[str, Any]] = None


class ExternalChannelConversationRead(APIModel):
    id: int
    ticket_id: int
    session_key: str
    channel: Optional[str] = None
    recipient: Optional[str] = None
    account_id: Optional[str] = None
    thread_id: Optional[str] = None
    last_cursor: Optional[int] = None
    last_message_id: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ExternalChannelTranscriptRead(APIModel):
    id: int
    conversation_id: int
    ticket_id: int
    session_key: str
    message_id: str
    role: Optional[str] = None
    author_name: Optional[str] = None
    body_text: Optional[str] = None
    received_at: Optional[datetime] = None
    created_at: datetime


class ExternalChannelSyncResult(APIModel):
    conversation: ExternalChannelConversationRead
    messages: list[ExternalChannelTranscriptRead]
    linked_ticket_id: int

class BackgroundJobRead(APIModel):
    id: int
    queue_name: str
    job_type: str
    status: str
    dedupe_key: Optional[str] = None
    attempt_count: int
    max_attempts: int
    next_run_at: Optional[datetime] = None
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ExternalChannelSyncEnqueueRequest(BaseModel):
    ticket_id: int
    session_key: str
    transcript_limit: Optional[int] = None
    dedupe: bool = True


class QueueSummaryRead(APIModel):
    pending_outbound: int
    dead_outbound: int
    pending_jobs: int
    dead_jobs: int
    external_channel_links: int
    external_channel_transcript_messages: int = 0
    external_channel_unresolved_events: int = 0


class ProductionReadinessRead(APIModel):
    app_env: str
    database_url_scheme: str
    is_postgres: bool
    storage_backend: str
    external_channel_transport: str
    metrics_enabled: bool
    external_channel_sync_enabled: bool
    external_channel_inbound_auto_sync_enabled: bool
    external_channel_links_count: int = 0
    external_channel_transcript_messages_count: int = 0
    external_channel_unresolved_events_count: int = 0
    outbound_email_production_pilot_enabled: bool = False
    outbound_email_active_accounts: int = 0
    outbound_email_successful_test_send_accounts: int = 0
    outbound_email_test_send_max_age_hours: int = 24
    warnings: list[str]



class ExternalChannelAttachmentReferenceRead(APIModel):
    id: int
    ticket_id: int
    transcript_message_id: int
    remote_attachment_id: str
    content_type: Optional[str] = None
    filename: Optional[str] = None
    storage_status: str
    storage_key: Optional[str] = None
    created_at: datetime


class ChannelAccountRead(APIModel):
    id: int
    provider: str
    account_id: str
    display_name: Optional[str] = None
    market_id: Optional[int] = None
    is_active: bool
    priority: int
    health_status: str
    fallback_account_id: Optional[str] = None
    updated_at: datetime


class ChannelAccountCreate(BaseModel):
    provider: str
    account_id: str
    display_name: Optional[str] = None
    market_id: Optional[int] = None
    priority: int = 100
    fallback_account_id: Optional[str] = None


class ChannelAccountUpdate(BaseModel):
    display_name: Optional[str] = None
    market_id: Optional[int] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    health_status: Optional[str] = None
    fallback_account_id: Optional[str] = None


OutboundEmailSecurityMode = Literal["starttls", "ssl", "plain"]


def _clean_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_required_string(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("value cannot be blank")
    return cleaned


class OutboundEmailAccountCreate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=160)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=4096)
    from_address: EmailStr
    reply_to: Optional[EmailStr] = None
    security_mode: OutboundEmailSecurityMode = "starttls"
    inbound_enabled: bool = False
    imap_host: Optional[str] = Field(default=None, max_length=253)
    imap_port: Optional[int] = Field(default=None, ge=1, le=65535)
    imap_username: Optional[str] = Field(default=None, max_length=255)
    imap_password: Optional[str] = Field(default=None, min_length=1, max_length=4096)
    imap_security_mode: Optional[OutboundEmailSecurityMode] = None
    imap_mailbox: Optional[str] = Field(default=None, max_length=120)
    market_id: Optional[int] = None
    priority: int = Field(default=100, ge=1, le=1000)
    is_active: bool = True

    @field_validator("display_name", mode="before")
    @classmethod
    def clean_display_name(cls, value):
        return _clean_optional_string(value)

    @field_validator("host", "username", "imap_host", "imap_username", "imap_mailbox")
    @classmethod
    def clean_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_required_string(value)

    @field_validator("password", "imap_password")
    @classmethod
    def validate_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("password cannot be blank")
        return value


class OutboundEmailAccountUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=160)
    host: Optional[str] = Field(default=None, min_length=1, max_length=253)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, min_length=1, max_length=4096)
    from_address: Optional[EmailStr] = None
    reply_to: Optional[EmailStr] = None
    security_mode: Optional[OutboundEmailSecurityMode] = None
    inbound_enabled: Optional[bool] = None
    imap_host: Optional[str] = Field(default=None, min_length=1, max_length=253)
    imap_port: Optional[int] = Field(default=None, ge=1, le=65535)
    imap_username: Optional[str] = Field(default=None, min_length=1, max_length=255)
    imap_password: Optional[str] = Field(default=None, min_length=1, max_length=4096)
    imap_security_mode: Optional[OutboundEmailSecurityMode] = None
    imap_mailbox: Optional[str] = Field(default=None, min_length=1, max_length=120)
    market_id: Optional[int] = None
    priority: Optional[int] = Field(default=None, ge=1, le=1000)
    is_active: Optional[bool] = None

    @field_validator("display_name", mode="before")
    @classmethod
    def clean_display_name(cls, value):
        return _clean_optional_string(value)

    @field_validator("host", "username", "imap_host", "imap_username", "imap_mailbox")
    @classmethod
    def clean_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_required_string(value)

    @field_validator("password", "imap_password")
    @classmethod
    def validate_password(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("password cannot be blank")
        return value


class OutboundEmailAccountRead(APIModel):
    id: int
    display_name: Optional[str] = None
    host: str
    port: int
    username: str
    from_address: str
    reply_to: Optional[str] = None
    security_mode: str
    inbound_enabled: bool = False
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    imap_security_mode: Optional[str] = None
    imap_mailbox: Optional[str] = None
    imap_sync_cursor: Optional[str] = None
    imap_last_seen_at: Optional[datetime] = None
    imap_last_status: Optional[str] = None
    imap_last_error: Optional[str] = None
    imap_last_sync_job_id: Optional[int] = None
    imap_password_configured: bool = False
    imap_password_mask: Optional[str] = None
    market_id: Optional[int] = None
    is_active: bool
    priority: int
    health_status: str
    last_test_status: Optional[str] = None
    last_test_error: Optional[str] = None
    last_test_at: Optional[datetime] = None
    password_configured: bool = False
    password_mask: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class OutboundEmailTestSendRequest(BaseModel):
    to_address: EmailStr
    subject: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = Field(default=None, max_length=4000)


class OutboundEmailTestSendRead(APIModel):
    ok: bool
    account_id: int
    provider_status: str
    failure_code: Optional[str] = None
    error_message: Optional[str] = None
    sent_at: Optional[datetime] = None
    health_status: str


class EmailMailboxSyncAccountStatus(APIModel):
    account_id: int
    display_name: Optional[str] = None
    from_address: str
    inbound_enabled: bool
    configured: bool
    imap_host: Optional[str] = None
    imap_mailbox: Optional[str] = None
    imap_sync_cursor: Optional[str] = None
    imap_last_seen_at: Optional[datetime] = None
    imap_last_status: Optional[str] = None
    imap_last_error: Optional[str] = None
    imap_last_sync_job_id: Optional[int] = None


class EmailMailboxSyncStatusRead(APIModel):
    generated_at: datetime
    daemon_enabled: bool
    interval_seconds: int
    enabled_accounts: int
    configured_accounts: int
    pending_jobs: int
    dead_jobs: int
    accounts: list[EmailMailboxSyncAccountStatus] = Field(default_factory=list)


class EmailMailboxSyncEnqueueRequest(BaseModel):
    account_id: Optional[int] = None


class EmailMailboxSyncEnqueueResponse(APIModel):
    ok: bool = True
    enqueued: int
    job_ids: list[int] = Field(default_factory=list)


class ExternalChannelRuntimeHealthRead(APIModel):
    sync_cursor: Optional[str] = None
    sync_daemon_last_seen_at: Optional[datetime] = None
    sync_daemon_status: Optional[str] = None
    stale_link_count: int
    external_channel_links_count: int = 0
    transcript_messages_count: int = 0
    unresolved_events_count: int = 0
    pending_sync_jobs: int
    dead_sync_jobs: int
    pending_attachment_jobs: int = 0
    dead_attachment_jobs: int = 0
    warnings: list[str]


class ExternalChannelConnectivityProbeRead(APIModel):
    deployment_mode: str
    transport: str
    command: Optional[str] = None
    url: Optional[str] = None
    token_auth_configured: bool = False
    password_auth_configured: bool = False
    bridge_started: bool = False
    conversations_tool_ok: bool = False
    conversations_seen: int = 0
    sample_session_key: Optional[str] = None
    warnings: list[str] = []


class MarketBulletinRead(APIModel):
    id: int
    market_id: Optional[int] = None
    country_code: Optional[str] = None
    title: str
    body: str
    summary: Optional[str] = None
    category: str
    channels_csv: Optional[str] = None
    audience: str
    severity: str
    auto_inject_to_ai: bool
    is_active: bool
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class MarketBulletinCreate(BaseModel):
    market_id: Optional[int] = None
    country_code: Optional[str] = None
    title: str
    body: str
    summary: Optional[str] = None
    category: str = "notice"
    channels_csv: Optional[str] = None
    audience: str = "customer"
    severity: str = "info"
    auto_inject_to_ai: bool = True
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class MarketBulletinUpdate(BaseModel):
    market_id: Optional[int] = None
    country_code: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    summary: Optional[str] = None
    category: Optional[str] = None
    channels_csv: Optional[str] = None
    audience: Optional[str] = None
    severity: Optional[str] = None
    auto_inject_to_ai: Optional[bool] = None
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class MarketBulletinImpactPreviewRequest(BaseModel):
    market_id: Optional[int] = None
    country_code: Optional[str] = None
    channels_csv: Optional[str] = None
    audience: str = "customer"
    auto_inject_to_ai: bool = True
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class MarketBulletinImpactChannelCount(APIModel):
    channel: str
    count: int


class MarketBulletinImpactTicket(APIModel):
    id: int
    ticket_no: str
    title: str
    status: str
    channel: str
    updated_at: datetime


class MarketBulletinImpactPreviewRead(APIModel):
    matching_tickets: int
    ready_to_reply_tickets: int
    channel_counts: list[MarketBulletinImpactChannelCount] = Field(default_factory=list)
    sample_tickets: list[MarketBulletinImpactTicket] = Field(default_factory=list)
    window_status: str
    scope_label: str
    auto_inject_to_ai: bool
    ai_context_enabled: bool


class AIConfigResourceRead(APIModel):
    id: int
    resource_key: str
    config_type: str
    name: str
    description: Optional[str] = None
    scope_type: str
    scope_value: Optional[str] = None
    market_id: Optional[int] = None
    is_active: bool
    draft_summary: Optional[str] = None
    draft_content_json: Optional[dict[str, Any]] = None
    published_summary: Optional[str] = None
    published_content_json: Optional[dict[str, Any]] = None
    published_version: int
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AIConfigResourceCreate(BaseModel):
    resource_key: str
    config_type: str
    name: str
    description: Optional[str] = None
    scope_type: str = "global"
    scope_value: Optional[str] = None
    market_id: Optional[int] = None
    is_active: bool = True
    draft_summary: Optional[str] = None
    draft_content_json: dict[str, Any] = Field(default_factory=dict)


class AIConfigResourceUpdate(BaseModel):
    resource_key: Optional[str] = None
    config_type: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    scope_type: Optional[str] = None
    scope_value: Optional[str] = None
    market_id: Optional[int] = None
    is_active: Optional[bool] = None
    draft_summary: Optional[str] = None
    draft_content_json: Optional[dict[str, Any]] = None


class AIConfigPublishRequest(BaseModel):
    notes: Optional[str] = None


class AIConfigVersionRead(APIModel):
    id: int
    resource_id: int
    version: int
    snapshot_json: dict[str, Any]
    summary: Optional[str] = None
    notes: Optional[str] = None
    published_by: Optional[int] = None
    published_at: datetime


class UserCreate(APIModel):
    username: str
    password: str = Field(min_length=6)
    display_name: str
    email: Optional[str] = None
    role: UserRole
    team_id: Optional[int] = None
    capabilities: list[str] = Field(default_factory=list)
