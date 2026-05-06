from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OperatorTaskRead(BaseModel):
    id: int
    source_type: str
    source_id: str | None = None
    ticket_id: int | None = None
    webchat_conversation_id: int | None = None
    unresolved_event_id: int | None = None
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
    projected_openclaw_unresolved: int = 0
    projected_webchat_handoff: int = 0
    created_total: int = 0
    skipped_existing: int = 0
