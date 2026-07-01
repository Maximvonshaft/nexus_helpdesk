from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .tool_registry import canonical_tool_name


AI_DECISION_SCHEMA_VERSION = "webchat_ai_decision_v1"

Intent = Literal[
    "unclear",
    "tracking",
    "handoff_request",
    "refusal_request",
    "address_change",
    "complaint",
    "general_support",
    "tracking_missing_number",
    "tracking_unresolved",
    "other",
]
RiskLevel = Literal["low", "medium", "high"]
NextAction = Literal["reply", "ask_clarifying_question", "call_tool", "request_handoff"]

_ALLOWED_INTENTS = {
    "unclear",
    "tracking",
    "handoff_request",
    "refusal_request",
    "address_change",
    "complaint",
    "general_support",
    "tracking_missing_number",
    "tracking_unresolved",
    "other",
}
_INTENT_ALIASES = {
    "greeting": "general_support",
    "handoff": "handoff_request",
    "human": "handoff_request",
    "human_request": "handoff_request",
    "refusal": "refusal_request",
    "return": "refusal_request",
    "refund": "complaint",
    "compensation": "complaint",
    "tracking_lookup": "tracking",
    "address_issue": "address_change",
    "delivery_reschedule": "general_support",
    "lost_or_damaged_parcel": "complaint",
    "general_question": "general_support",
}
_ALLOWED_RISK_LEVELS = {"low", "medium", "high"}
_ALLOWED_NEXT_ACTIONS = {"reply", "ask_clarifying_question", "call_tool", "request_handoff"}


def normalize_intent(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = _INTENT_ALIASES.get(cleaned, cleaned)
    return cleaned if cleaned in _ALLOWED_INTENTS else "other"


def normalize_risk_level(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in _ALLOWED_RISK_LEVELS else "low"


def normalize_next_action(value: Any, *, handoff_required: bool = False, has_tool_calls: bool = False, intent: str | None = None) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned in _ALLOWED_NEXT_ACTIONS:
        return cleaned
    if handoff_required:
        return "request_handoff"
    if has_tool_calls:
        return "call_tool"
    if intent in {"unclear", "tracking_missing_number"}:
        return "ask_clarifying_question"
    return "reply"


class AIDecisionToolCall(BaseModel):
    """AI-requested tool call proposal.

    This is a proposal only.  The backend still validates the tool contract and
    executes through Tool Executor; the AI never writes database state directly.
    """

    model_config = ConfigDict(extra="ignore")

    tool_name: str = Field(min_length=1, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=240)
    reason: str | None = Field(default=None, max_length=500)
    requires_confirmation: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _compat_tool_name(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("tool_name"):
            data["tool_name"] = data.get("name") or data.get("tool") or data.get("type")
        if not isinstance(data.get("arguments"), dict):
            data["arguments"] = {}
        return data

    @field_validator("tool_name")
    @classmethod
    def _clean_tool_name(cls, value: str) -> str:
        cleaned = canonical_tool_name(value)
        if not cleaned:
            raise ValueError("tool_name is required")
        return cleaned


class AIDecisionEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = Field(min_length=1, max_length=160)
    evidence_type: str | None = Field(default=None, max_length=120)
    evidence_id: str | None = Field(default=None, max_length=240)
    fact_evidence_present: bool | None = None
    policy_evidence_present: bool | None = None
    tracking_number_hash: str | None = Field(default=None, max_length=120)
    raw_tracking_number_exposed: bool = False


class AIDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_reply: str = Field(min_length=1, max_length=2000)
    intent: Intent = "unclear"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_level: RiskLevel = "low"
    next_action: NextAction = "reply"
    handoff_required: bool = False
    handoff_reason: str | None = Field(default=None, max_length=240)
    tool_calls: list[AIDecisionToolCall] = Field(default_factory=list, max_length=12)
    evidence_used: list[AIDecisionEvidence] = Field(default_factory=list, max_length=20)
    safety_notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def _compat_reply_and_tools(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("customer_reply") is None and data.get("reply") is not None:
            data["customer_reply"] = data.get("reply")
        if data.get("tool_calls") is None:
            data["tool_calls"] = []
        if data.get("evidence_used") is None:
            data["evidence_used"] = []
        if data.get("safety_notes") is None:
            data["safety_notes"] = []
        return data

    @field_validator("intent", mode="before")
    @classmethod
    def _normalize_intent(cls, value: Any) -> str:
        return normalize_intent(value)

    @field_validator("risk_level", mode="before")
    @classmethod
    def _normalize_risk(cls, value: Any) -> str:
        return normalize_risk_level(value)

    @field_validator("next_action", mode="before")
    @classmethod
    def _normalize_next(cls, value: Any) -> str:
        cleaned = str(value or "").strip().lower()
        return cleaned if cleaned in _ALLOWED_NEXT_ACTIONS else "reply"

    @field_validator("customer_reply")
    @classmethod
    def _clean_reply(cls, value: str) -> str:
        cleaned = " ".join(str(value or "").strip().split())
        if not cleaned:
            raise ValueError("customer_reply is required")
        return cleaned[:2000]

    @field_validator("safety_notes", mode="before")
    @classmethod
    def _clean_safety_notes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        raw_items = value if isinstance(value, list) else [value]
        notes: list[str] = []
        for item in raw_items[:20]:
            cleaned = " ".join(str(item or "").strip().split())
            if cleaned:
                notes.append(cleaned[:300])
        return notes

    @model_validator(mode="after")
    def _derive_next_action(self) -> "AIDecision":
        self.next_action = normalize_next_action(
            self.next_action,
            handoff_required=self.handoff_required,
            has_tool_calls=bool(self.tool_calls),
            intent=self.intent,
        )  # type: ignore[assignment]
        if self.handoff_required and not self.handoff_reason:
            self.handoff_reason = f"{self.intent}_requires_human_review"  # type: ignore[misc]
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
