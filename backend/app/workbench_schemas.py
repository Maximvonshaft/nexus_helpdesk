from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .utils.time import format_utc


class WorkbenchModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_common_types(self, value):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class WorkbenchUser(WorkbenchModel):
    id: int
    username: str
    display_name: str
    role: str
    team_id: Optional[int] = None
    capabilities: list[str] = Field(default_factory=list)


class WorkbenchMetric(WorkbenchModel):
    key: str
    label: str
    value: int
    tone: str = "default"
    hint: Optional[str] = None
    target_route: Optional[str] = None


class WorkbenchTask(WorkbenchModel):
    id: str
    title: str
    count: int
    severity: str = "default"
    source: str
    next_action: str
    target_route: str


class WorkbenchQueueItem(WorkbenchModel):
    id: str
    kind: str
    ticket_id: Optional[int] = None
    ticket_no: Optional[str] = None
    title: str
    customer_name: Optional[str] = None
    channel: Optional[str] = None
    status: str
    priority: Optional[str] = None
    assignee_name: Optional[str] = None
    team_name: Optional[str] = None
    due_at: Optional[datetime] = None
    overdue: bool = False
    waiting_seconds: Optional[int] = None
    recommended_action: str
    target_route: str
    updated_at: Optional[datetime] = None


class WorkbenchInteractionState(WorkbenchModel):
    key: str
    label: str
    count: int
    tone: str = "default"
    target_route: str


class WorkbenchSummaryRead(WorkbenchModel):
    generated_at: datetime
    user: WorkbenchUser
    metrics: list[WorkbenchMetric]
    tasks: list[WorkbenchTask]
    queue: list[WorkbenchQueueItem]
    sla_risks: list[WorkbenchQueueItem]
    interaction_states: list[WorkbenchInteractionState]
    data_sources: list[str]
