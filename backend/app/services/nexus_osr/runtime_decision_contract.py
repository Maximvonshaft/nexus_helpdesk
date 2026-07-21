from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BusinessReplyType(StrEnum):
    """Persisted audit category for canonical Tool execution only."""

    TOOL_ACTION_RESULT = "tool_action_result"


class RuntimeAction(StrEnum):
    """Persisted action category for canonical Tool execution only."""

    CALL_TOOL = "call_tool"


@dataclass(frozen=True)
class RuntimeToolAction:
    """Bounded canonical Tool action used by the single Tool Executor."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    executed: bool = False
    result_source_id: str | None = None


@dataclass(frozen=True)
class RuntimeDecision:
    """Sanitized audit envelope for one canonical Tool execution decision.

    This is not an Agent output contract and does not evaluate customer-visible
    business replies. `nexus.agent_turn.v1` is the only model/runtime contract.
    """

    business_reply_type: BusinessReplyType | str = BusinessReplyType.TOOL_ACTION_RESULT
    next_action: RuntimeAction | str = RuntimeAction.CALL_TOOL
    customer_reply: str | None = None
    language: str | None = None
    risk_level: str = "low"
    tool_actions: list[RuntimeToolAction] = field(default_factory=list)
    handoff_required: bool = False
    ticket_required: bool = False
    routing_required: bool = False
    audit_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeDecisionViolation:
    code: str
    message: str
    severity: str = "high"


@dataclass(frozen=True)
class RuntimeDecisionEvaluation:
    allowed: bool
    violations: list[RuntimeDecisionViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def require_allowed(self) -> None:
        if not self.allowed:
            joined = "; ".join(
                f"{item.code}: {item.message}" for item in self.violations
            )
            raise ValueError(joined or "Tool execution decision is not allowed")
