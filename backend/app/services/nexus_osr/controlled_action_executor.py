from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..agent_runtime.execution_scope import current_agent_tool_handler
from .case_context import CaseContext
from .policies import ToolExecutionPolicy, ToolPolicyDecision
from .runtime_decision_contract import RuntimeToolAction


class ActionHandler(Protocol):
    def __call__(self, request: "ActionExecutionRequest") -> "ActionExecutionResult": ...


@dataclass(frozen=True)
class ActionExecutionRequest:
    action: RuntimeToolAction
    channel: str | None = None
    country_code: str | None = None
    case_context: CaseContext | None = None
    idempotency_key: str | None = None
    audit_context: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tracking_number(self) -> bool:
        return bool(
            self.case_context
            and (
                self.case_context.tracking_number_hash
                or self.case_context.safe_tracking_reference
            )
        )

    @property
    def has_contact(self) -> bool:
        return bool(self.case_context and self.case_context.contact_methods)


@dataclass(frozen=True)
class ActionExecutionResult:
    ok: bool
    tool_name: str
    status: str
    summary: dict[str, Any] = field(default_factory=dict)
    customer_visible_summary: str | None = None
    case_context: CaseContext | None = None
    policy_decision: ToolPolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None
    elapsed_ms: int = 0


class ControlledActionExecutor:
    """The single policy-gated canonical Tool dispatcher.

    Core handlers are supplied by ``tool_execution_service_core``. Agent-only
    extensions are bound through a request-local ContextVar and resolved here,
    so no public module mutates the private executor or creates a second
    dispatcher. Request-local handlers intentionally take precedence for tools
    such as ``knowledge.search`` whose candidates are constrained by an
    immutable Agent Release.

    Handler duration is measured here because this is the one point at which a
    policy-approved Tool crosses into its production handler. The existing
    persistence/audit layer may record this value but does not maintain a second
    execution clock.
    """

    def __init__(
        self,
        *,
        policies: dict[str, ToolExecutionPolicy],
        handlers: dict[str, ActionHandler],
        allowed_high_risk_write_tools: set[str] | frozenset[str] | None = None,
    ):
        self._policies = dict(policies)
        self._handlers = dict(handlers)
        self._allowed_high_risk_write_tools = set(
            allowed_high_risk_write_tools or set()
        )

    def execute(self, request: ActionExecutionRequest) -> ActionExecutionResult:
        policy = self._policies.get(request.action.tool_name)
        if policy is None:
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="blocked",
                error_code="tool_policy_missing",
                error_message="No ToolExecutionPolicy is configured for this Tool.",
            )
        policy_decision = policy.evaluate(
            channel=request.channel,
            country_code=request.country_code,
            has_tracking_number=request.has_tracking_number,
            has_contact=request.has_contact,
        )
        if not policy_decision.allowed:
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="blocked",
                policy_decision=policy_decision,
                error_code=policy_decision.reason,
                error_message=policy_decision.reason,
                summary={"missing_requirements": policy_decision.missing_requirements},
            )
        if (
            str(policy.risk_level or "").lower() == "high"
            and policy.tool_name not in self._allowed_high_risk_write_tools
        ):
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="blocked",
                policy_decision=policy_decision,
                error_code="high_risk_write_tool_blocked",
                error_message=(
                    "High-risk write Tools require explicit server configuration "
                    "before execution."
                ),
            )
        if (
            policy_decision.requires_customer_confirmation
            and not bool(
                request.audit_context.get("customer_confirmation_granted")
            )
        ):
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="confirmation_required",
                policy_decision=policy_decision,
                error_code="customer_confirmation_required",
                error_message=(
                    "Customer confirmation is required before this Tool can execute."
                ),
            )
        if (
            policy_decision.requires_human_confirmation
            and not bool(request.audit_context.get("human_confirmation_granted"))
        ):
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="confirmation_required",
                policy_decision=policy_decision,
                error_code="human_confirmation_required",
                error_message=(
                    "Human confirmation is required before this Tool can execute."
                ),
            )
        handler = current_agent_tool_handler(request.action.tool_name)
        if handler is None:
            handler = self._handlers.get(request.action.tool_name)
        if handler is None:
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="blocked",
                policy_decision=policy_decision,
                error_code="tool_handler_missing",
                error_message=(
                    "No production handler is registered for this canonical Tool."
                ),
            )
        started = time.monotonic()
        result = handler(request)
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return ActionExecutionResult(
            ok=result.ok,
            tool_name=result.tool_name,
            status=result.status,
            summary=dict(result.summary or {}),
            customer_visible_summary=result.customer_visible_summary,
            case_context=result.case_context,
            policy_decision=policy_decision,
            error_code=result.error_code,
            error_message=result.error_message,
            elapsed_ms=elapsed_ms,
        )
