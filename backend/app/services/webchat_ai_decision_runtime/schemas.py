from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .tool_registry import get_tool_contract

AI_DECISION_SCHEMA_VERSION = "nexus.agent_turn.v1"
RiskLevel = Literal["low", "medium", "high"]
NextAction = Literal["reply", "ask_clarifying_question", "call_tool", "request_handoff"]


class AIDecisionToolCall(BaseModel):
    """A model-proposed Tool call. The backend remains execution authority."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=240)
    reason: str | None = Field(default=None, max_length=500)
    requires_confirmation: bool | None = None

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        cleaned = " ".join(str(value or "").strip().split())
        if not cleaned or get_tool_contract(cleaned) is None:
            raise ValueError("tool_name must reference a registered canonical Tool")
        return cleaned


class AIDecisionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=160)
    evidence_id: str | None = Field(default=None, max_length=240)
    observation_status: str | None = Field(default=None, max_length=80)


class AIDecision(BaseModel):
    """Canonical model turn for direct replies and Tool requests."""

    model_config = ConfigDict(extra="forbid")

    customer_reply: str | None = Field(default=None, max_length=4000)
    intent: str = Field(default="general_support", min_length=1, max_length=80)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_level: RiskLevel = "low"
    next_action: NextAction = "reply"
    handoff_required: bool = False
    handoff_reason: str | None = Field(default=None, max_length=240)
    tool_calls: list[AIDecisionToolCall] = Field(default_factory=list, max_length=12)
    evidence_used: list[AIDecisionEvidence] = Field(default_factory=list, max_length=20)
    safety_notes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("customer_reply")
    @classmethod
    def _clean_reply(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(str(value).strip().split())
        return cleaned[:4000] if cleaned else None

    @field_validator("intent")
    @classmethod
    def _clean_intent(cls, value: str) -> str:
        cleaned = "_".join(str(value or "general_support").strip().lower().split())
        return cleaned[:80] or "general_support"

    @field_validator("safety_notes", mode="before")
    @classmethod
    def _clean_notes(cls, value: Any) -> list[str]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        return [
            " ".join(str(item).strip().split())[:300]
            for item in values[:20]
            if str(item or "").strip()
        ]

    @model_validator(mode="after")
    def _validate_turn_shape(self) -> "AIDecision":
        if self.handoff_required and not self.handoff_reason:
            self.handoff_reason = "human_review_requested"  # type: ignore[assignment]
        if self.next_action == "call_tool":
            if not self.tool_calls:
                raise ValueError("next_action=call_tool requires tool_calls")
            if self.customer_reply:
                raise ValueError("Tool-call turns cannot contain a customer_reply")
            return self
        if self.tool_calls:
            raise ValueError("tool_calls require next_action=call_tool")
        if not self.customer_reply:
            raise ValueError("final turns require customer_reply")
        if self.handoff_required and self.next_action == "reply":
            self.next_action = "request_handoff"  # type: ignore[assignment]
        return self

    def safe_public_summary(self) -> dict[str, Any]:
        return {
            "schema_version": AI_DECISION_SCHEMA_VERSION,
            "intent": self.intent,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "next_action": self.next_action,
            "handoff_required": self.handoff_required,
            "handoff_reason": self.handoff_reason,
            "tool_calls": [
                {
                    "tool_name": call.tool_name,
                    "idempotency_key_present": bool(call.idempotency_key),
                    "requires_confirmation": call.requires_confirmation,
                }
                for call in self.tool_calls
            ],
            "evidence_used": [item.model_dump(exclude_none=True) for item in self.evidence_used],
            "safety_notes": list(self.safety_notes),
        }
