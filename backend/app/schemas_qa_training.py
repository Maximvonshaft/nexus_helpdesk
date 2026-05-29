from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .schemas import APIModel


class QASampleRead(APIModel):
    ticket_id: int
    ticket_no: Optional[str] = None
    title: str
    sample_channel: str
    sample_ref: Optional[str] = None
    customer_name: Optional[str] = None
    agent_id: Optional[int] = None
    agent_name: Optional[str] = None
    status: str
    priority: str
    ai_pre_score: int
    risks: list[str] = Field(default_factory=list)
    feedback: Optional[str] = None
    appeal_status: str = "not_started"
    knowledge_gap_summary: Optional[str] = None
    updated_at: datetime
    reviewed_at: Optional[datetime] = None


class QAQueueSummary(APIModel):
    total_samples: int
    needs_review: int
    reviewed: int
    average_ai_pre_score: int
    open_training_tasks: int
    knowledge_gap_tasks: int


class QAQueueRead(APIModel):
    samples: list[QASampleRead]
    summary: QAQueueSummary


class QATrainingTaskRead(APIModel):
    id: int
    review_id: Optional[int] = None
    ticket_id: int
    agent_id: Optional[int] = None
    owner_id: Optional[int] = None
    task_type: str
    status: str
    summary: str
    knowledge_gap_summary: Optional[str] = None
    due_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class QAReviewRead(APIModel):
    id: int
    ticket_id: int
    sample_channel: str
    sample_ref: Optional[str] = None
    reviewer_id: Optional[int] = None
    agent_id: Optional[int] = None
    status: str
    ai_pre_score: int
    final_score: Optional[int] = None
    risks: list[str] = Field(default_factory=list)
    feedback: Optional[str] = None
    knowledge_gap_summary: Optional[str] = None
    appeal_status: str
    training_task: Optional[QATrainingTaskRead] = None
    created_at: datetime
    updated_at: datetime


class QAReviewCreate(BaseModel):
    ticket_id: int
    final_score: int = Field(ge=0, le=100)
    risks: list[str] = Field(default_factory=list)
    feedback: str = Field(min_length=1, max_length=4000)
    knowledge_gap_summary: Optional[str] = Field(default=None, max_length=4000)
    appeal_status: str = "not_started"
    create_training_task: bool = True
    coaching_summary: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("feedback", "knowledge_gap_summary", "coaching_summary", "appeal_status", mode="before")
    @classmethod
    def _strip_optional_text(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("risks", mode="before")
    @classmethod
    def _normalize_risks(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = value.replace(",", "\n").splitlines()
        seen: set[str] = set()
        risks: list[str] = []
        for item in value:
            normalized = str(item).strip().lower().replace(" ", "_")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            risks.append(normalized[:80])
        return risks
