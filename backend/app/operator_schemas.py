from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OperatorTaskRead(BaseModel):
    id: int
    source_type: str
    source_id: str | None = None
    ticket_id: int | None = None
    webchat_conversation_id: int | None = None
    task_type: str
    status: str
    priority: int
    assignee_id: int | None = None
    reason_code: str | None = None
    payload_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Admin-only redacted payload; never includes raw visitor token, session key, email, phone, or full raw error.",
    )
    created_at: str | None = None
    updated_at: str | None = None
    resolved_at: str | None = None


class OperatorTaskListResponse(BaseModel):
    items: list[OperatorTaskRead]
    next_cursor: str | None = None
    filters: dict[str, str | None] = Field(default_factory=dict)


class OperatorTaskTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None


class OperatorTaskTransitionResponse(BaseModel):
    task: OperatorTaskRead
    replay_result: dict[str, Any] | None = None


class OperatorQueueProjectResponse(BaseModel):
    projected_webchat_handoff: int = 0
    created_total: int = 0
    skipped_existing: int = 0


class OperatorQueueScopeGrantUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    tenant_key: str = Field(min_length=1, max_length=80)
    country_code: str = Field(min_length=2, max_length=16)
    channel_key: str = Field(min_length=1, max_length=40)
    enabled: bool = True


class OperatorQueueScopeGrantRead(BaseModel):
    id: int
    user_id: int
    tenant_hash: str = Field(min_length=12, max_length=12)
    country_code: str
    channel_key: str
    enabled: bool
    created_at: str
    updated_at: str


class OperatorQueueCurrentScopeRead(BaseModel):
    tenant_key: str = Field(min_length=1, max_length=80)
    tenant_hash: str = Field(min_length=12, max_length=12)
    country_code: str = Field(min_length=2, max_length=16)
    channel_key: str = Field(min_length=1, max_length=40)


class OperatorQueueCurrentScopesResponse(BaseModel):
    items: list[OperatorQueueCurrentScopeRead] = Field(default_factory=list)


class UnifiedQueueOwner(BaseModel):
    kind: Literal["user", "team", "worker_lease", "unassigned"]
    user_id: int | None = Field(default=None, gt=0)
    team_id: int | None = Field(default=None, gt=0)


class UnifiedQueueSLA(BaseModel):
    state: Literal["healthy", "at_risk", "breached", "paused", "stale", "not_applicable", "unavailable"]
    due_at: str | None = None
    seconds_remaining: int | None = None


class UnifiedQueueRetry(BaseModel):
    state: Literal["not_applicable", "pending", "processing", "retry_scheduled", "exhausted", "settled"]
    attempt_count: int = Field(ge=0, le=1000)
    max_attempts: int = Field(ge=0, le=1000)
    next_retry_at: str | None = None
    error_category: str | None = Field(default=None, max_length=80)


class UnifiedQueueSourceLinks(BaseModel):
    ticket: str | None = Field(default=None, max_length=120)
    conversation: str | None = Field(default=None, max_length=120)
    handoff: str | None = Field(default=None, max_length=120)
    dispatch: str | None = Field(default=None, max_length=120)


class UnifiedOperatorQueueItem(BaseModel):
    queue_id: str = Field(max_length=80)
    case_key: str | None = Field(default=None, max_length=80)
    source_type: Literal["handoff", "ticket", "dispatch"]
    source_id: int = Field(gt=0)
    ticket_id: int | None = Field(default=None, gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    country_code: str = Field(max_length=16)
    channel_key: str = Field(max_length=40)
    state: Literal["active", "terminal"]
    source_status: str = Field(max_length=40)
    reopened: bool = False
    priority: Literal["low", "medium", "high", "urgent"]
    owner: UnifiedQueueOwner
    sla: UnifiedQueueSLA
    retry: UnifiedQueueRetry
    created_at: str
    updated_at: str
    source_links: UnifiedQueueSourceLinks


class UnifiedQueueScope(BaseModel):
    tenant_hash: str = Field(min_length=12, max_length=12)
    country_code: str = Field(max_length=16)
    channel_key: str = Field(max_length=40)


class UnifiedQueueFilters(BaseModel):
    state: str | None = Field(default=None, max_length=20)
    source_type: str | None = Field(default=None, max_length=20)
    owner: str | None = Field(default=None, max_length=20)
    priority: str | None = Field(default=None, max_length=20)
    sla: str | None = Field(default=None, max_length=24)
    retry: str | None = Field(default=None, max_length=24)
    sort: Literal["oldest", "newest"]


class UnifiedOperatorQueueResponse(BaseModel):
    items: list[UnifiedOperatorQueueItem]
    next_cursor: str | None = None
    scope: UnifiedQueueScope
    filters: UnifiedQueueFilters
