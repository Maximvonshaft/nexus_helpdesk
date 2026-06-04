from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class EvidenceClass(str, Enum):
    POLICY = "policy"
    SOP = "sop"
    FAQ = "faq"
    STRUCTURED_FACT = "structured_fact"
    LIVE_STATUS = "live_status"
    TOOL_RESULT = "tool_result"
    CUSTOMER_CLAIM = "customer_claim"
    IDENTITY = "identity"
    UNSAFE_OR_UNVERIFIED = "unsafe_or_unverified"


class ActionBoundary(str, Enum):
    NONE = "none"
    VERIFICATION_REQUIRED = "verification_required"
    TOOL_REQUIRED = "tool_required"
    HUMAN_REQUIRED = "human_required"
    FORBIDDEN = "forbidden"


class AnswerPlanType(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    GUIDED_ANSWER = "guided_answer"
    CLARIFY = "clarify"
    TOOL_CALL = "tool_call"
    TOOL_PREPARE = "tool_prepare"
    WORK_ORDER_CREATE = "work_order_create"
    HANDOFF = "handoff"
    DENY_UNSUPPORTED = "deny_unsupported"
    DENY_UNVERIFIED = "deny_unverified"
    SAFE_GENERAL_REPLY = "safe_general_reply"


@dataclass(frozen=True)
class DomainIntent:
    key: str
    domain: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    rewrite_terms: tuple[str, ...] = ()
    evidence_class: EvidenceClass = EvidenceClass.FAQ
    action_boundary: ActionBoundary = ActionBoundary.NONE
    allowed_plan_types: tuple[AnswerPlanType, ...] = (AnswerPlanType.GUIDED_ANSWER,)
    requires_verification: bool = False
    requires_tool_boundary: bool = False
    tool_keys: tuple[str, ...] = ()
    negative_aliases: tuple[str, ...] = ()

    @property
    def full_key(self) -> str:
        return f"{self.domain}.{self.key}"


@dataclass(frozen=True)
class DomainEntity:
    key: str
    value: Any
    confidence: float = 1.0
    source: str = "rule"


@dataclass(frozen=True)
class QueryRewriteResult:
    normalized_query: str
    rewrite_terms: tuple[str, ...] = ()
    expanded_query: str = ""

    def as_trace(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DomainQueryUnderstandingResult:
    raw_query: str
    normalized_query: str
    domain: str | None = None
    primary_intent: str | None = None
    secondary_intents: tuple[str, ...] = ()
    entities: tuple[DomainEntity, ...] = ()
    rewrite: QueryRewriteResult | None = None
    evidence_class: EvidenceClass | None = None
    action_boundary: ActionBoundary = ActionBoundary.NONE
    allowed_plan_types: tuple[AnswerPlanType, ...] = ()
    requires_verification: bool = False
    requires_tool_boundary: bool = False
    confidence: float = 0.0
    matched_aliases: tuple[str, ...] = ()
    shadow_mode: bool = True

    @property
    def has_business_intent(self) -> bool:
        return bool(self.domain and self.primary_intent)

    def as_trace(self) -> dict[str, Any]:
        data = asdict(self)
        if self.evidence_class is not None:
            data["evidence_class"] = self.evidence_class.value
        data["action_boundary"] = self.action_boundary.value
        data["allowed_plan_types"] = [item.value for item in self.allowed_plan_types]
        data["entities"] = [asdict(entity) for entity in self.entities]
        data["rewrite"] = self.rewrite.as_trace() if self.rewrite else None
        return data


@dataclass(frozen=True)
class RankedCandidate:
    item_key: str
    title: str = ""
    text: str = ""
    score: float = 0.0
    domain: str | None = None
    intent_keys: tuple[str, ...] = ()
    evidence_class: EvidenceClass | None = None
    action_boundary: ActionBoundary = ActionBoundary.NONE
    answer_plan_type: AnswerPlanType | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)

    def with_score(self, score: float, breakdown: dict[str, float]) -> "RankedCandidate":
        return RankedCandidate(
            item_key=self.item_key,
            title=self.title,
            text=self.text,
            score=score,
            domain=self.domain,
            intent_keys=self.intent_keys,
            evidence_class=self.evidence_class,
            action_boundary=self.action_boundary,
            answer_plan_type=self.answer_plan_type,
            metadata=self.metadata,
            score_breakdown=breakdown,
        )

    def as_trace(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_class"] = self.evidence_class.value if self.evidence_class else None
        data["action_boundary"] = self.action_boundary.value
        data["answer_plan_type"] = self.answer_plan_type.value if self.answer_plan_type else None
        return data


@dataclass(frozen=True)
class DomainGuardDecision:
    allowed: bool
    reason: str
    severity: str = "info"
    candidate: RankedCandidate | None = None

    def as_trace(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "severity": self.severity,
            "candidate": self.candidate.as_trace() if self.candidate else None,
        }


@dataclass(frozen=True)
class AnswerPlan:
    plan_type: AnswerPlanType
    reason: str
    requires_verification: bool = False
    requires_tool: bool = False
    requires_handoff: bool = False
    allowed_tools: tuple[str, ...] = ()
    evidence_class: EvidenceClass | None = None
    action_boundary: ActionBoundary = ActionBoundary.NONE
    safe_to_answer_from_kb: bool = True

    def as_trace(self) -> dict[str, Any]:
        return {
            "plan_type": self.plan_type.value,
            "reason": self.reason,
            "requires_verification": self.requires_verification,
            "requires_tool": self.requires_tool,
            "requires_handoff": self.requires_handoff,
            "allowed_tools": list(self.allowed_tools),
            "evidence_class": self.evidence_class.value if self.evidence_class else None,
            "action_boundary": self.action_boundary.value,
            "safe_to_answer_from_kb": self.safe_to_answer_from_kb,
        }
