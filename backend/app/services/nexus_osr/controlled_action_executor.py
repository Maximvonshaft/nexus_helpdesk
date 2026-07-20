from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

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
        return bool(self.case_context and (self.case_context.tracking_number_hash or self.case_context.safe_tracking_reference))

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


class ControlledActionExecutor:
    """Policy-gated execution harness for Nexus OSR actions.

    This class does not directly know about WebChat, WhatsApp, SQLAlchemy, or MCP.
    It validates the action against product policy and delegates the actual side
    effect to a registered handler. The handler is where repository-specific
    ticket/MCP/WhatsApp integrations should be connected.
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
        self._allowed_high_risk_write_tools = set(allowed_high_risk_write_tools or set())

    def execute(self, request: ActionExecutionRequest) -> ActionExecutionResult:
        policy = self._policies.get(request.action.tool_name)
        if policy is None:
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="blocked",
                error_code="tool_policy_missing",
                error_message="No ToolExecutionPolicy is configured for this tool.",
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
        if str(policy.risk_level or "").lower() == "high" and policy.tool_name not in self._allowed_high_risk_write_tools:
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="blocked",
                policy_decision=policy_decision,
                error_code="high_risk_write_tool_blocked",
                error_message="High-risk write tools require explicit test or operator configuration before execution.",
            )
        if (
            policy_decision.requires_customer_confirmation
            and not bool(request.audit_context.get("customer_confirmation_granted"))
        ):
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="confirmation_required",
                policy_decision=policy_decision,
                error_code="customer_confirmation_required",
                error_message="Customer confirmation is required before this action can execute.",
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
                error_message="Human confirmation is required before this action can execute.",
            )
        handler = self._handlers.get(request.action.tool_name)
        if handler is None:
            return ActionExecutionResult(
                ok=False,
                tool_name=request.action.tool_name,
                status="blocked",
                policy_decision=policy_decision,
                error_code="tool_handler_missing",
                error_message="No controlled action handler is registered for this tool.",
            )
        result = handler(request)
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
        )


def ticket_create_handler(request: ActionExecutionRequest) -> ActionExecutionResult:
    """Framework-light ticket.create handler for tests and future integration.

    The production integration should replace this with a handler that creates or
    reuses a real Ticket row. This handler still encodes the product contract:
    it requires a CaseContext, marks the ticket as created, and returns a safe
    customer-visible summary.
    """

    if request.case_context is None:
        return ActionExecutionResult(False, request.action.tool_name, "failed", error_code="case_context_required")
    ticket_id = request.action.arguments.get("ticket_id") or request.idempotency_key or "pending-ticket"
    next_context = request.case_context.mark_ticket_created(ticket_id)
    return ActionExecutionResult(
        ok=True,
        tool_name=request.action.tool_name,
        status="executed",
        summary={"ticket_id": ticket_id, "idempotency_key": request.idempotency_key},
        customer_visible_summary="A support ticket has been created for follow-up.",
        case_context=next_context,
    )


def handoff_request_handler(request: ActionExecutionRequest) -> ActionExecutionResult:
    if request.case_context is None:
        return ActionExecutionResult(False, request.action.tool_name, "failed", error_code="case_context_required")
    reason = request.action.arguments.get("reason") or "human_review_required"
    next_context = request.case_context.mark_handoff_requested(summary=str(reason))
    return ActionExecutionResult(
        ok=True,
        tool_name=request.action.tool_name,
        status="executed",
        summary={"reason": reason},
        customer_visible_summary="I will connect this case to a human support agent.",
        case_context=next_context,
    )


def default_handlers() -> dict[str, ActionHandler]:
    return {
        "ticket.create": ticket_create_handler,
        "handoff.request.create": handoff_request_handler,
    }
