from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer

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
    body: str


class OutboundSendRequest(BaseModel):
    channel: SourceChannel
    body: str


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
    body: str
    provider_status: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 0
    sent_at: Optional[datetime] = None
    created_at: datetime


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
    openclaw_conversation: Optional[OpenClawConversationRead] = None
    openclaw_transcript: list[OpenClawTranscriptRead] = Field(default_factory=list)
    openclaw_attachment_references: list["OpenClawAttachmentReferenceRead"] = Field(default_factory=list)
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


class OpenClawLinkRequest(BaseModel):
    ticket_id: int
    session_key: str
    channel: Optional[str] = None
    recipient: Optional[str] = None
    account_id: Optional[str] = None
    thread_id: Optional[str] = None
    route: Optional[dict[str, Any]] = None


class OpenClawConversationRead(APIModel):
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


class OpenClawTranscriptRead(APIModel):
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


class OpenClawSyncResult(APIModel):
    conversation: OpenClawConversationRead
    messages: list[OpenClawTranscriptRead]
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


class OpenClawSyncEnqueueRequest(BaseModel):
    ticket_id: int
    session_key: str
    transcript_limit: Optional[int] = None
    dedupe: bool = True


class QueueSummaryRead(APIModel):
    pending_outbound: int
    dead_outbound: int
    pending_jobs: int
    dead_jobs: int
    openclaw_links: int


class ProductionReadinessRead(APIModel):
    app_env: str
    database_url_scheme: str
    is_postgres: bool
    storage_backend: str
    openclaw_transport: str
    metrics_enabled: bool
    openclaw_sync_enabled: bool
    warnings: list[str]



class OpenClawAttachmentReferenceRead(APIModel):
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


class OpenClawRuntimeHealthRead(APIModel):
    sync_cursor: Optional[str] = None
    sync_daemon_last_seen_at: Optional[datetime] = None
    sync_daemon_status: Optional[str] = None
    stale_link_count: int
    pending_sync_jobs: int
    dead_sync_jobs: int
    pending_attachment_jobs: int = 0
    dead_attachment_jobs: int = 0
    warnings: list[str]


class OpenClawConnectivityProbeRead(APIModel):
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
    password: str
    display_name: str
    email: Optional[str] = None
    role: UserRole
    team_id: Optional[int] = None
    capabilities: list[str] = Field(default_factory=list)
