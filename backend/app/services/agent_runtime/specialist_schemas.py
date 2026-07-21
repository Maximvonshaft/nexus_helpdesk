from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SpecialistName = Literal[
    "knowledge_researcher",
    "policy_reviewer",
    "case_summarizer",
    "translation_reviewer",
    "data_analyst",
]


class SpecialistFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("evidence_refs")
    @classmethod
    def validate_refs(cls, value: list[str]) -> list[str]:
        output: list[str] = []
        for item in value:
            cleaned = str(item or "").strip()[:160]
            if cleaned and cleaned not in output:
                output.append(cleaned)
        return output[:12]


class SpecialistResult(BaseModel):
    """Read-only specialist evidence returned to the parent Agent.

    It is never customer-visible and has no Tool calls or action-success fields.
    """

    model_config = ConfigDict(extra="forbid")

    specialist: SpecialistName
    summary: str = Field(min_length=1, max_length=2000)
    findings: list[SpecialistFinding] = Field(default_factory=list, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)
    recommended_action: str | None = Field(default=None, max_length=1200)
    needs_human_review: bool = False

    @field_validator("risks")
    @classmethod
    def validate_risks(cls, value: list[str]) -> list[str]:
        output: list[str] = []
        for item in value:
            cleaned = str(item or "").strip()[:500]
            if cleaned and cleaned not in output:
                output.append(cleaned)
        return output[:12]
