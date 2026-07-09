from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ...models import Customer, Ticket
from ...webchat_models import WebchatConversation
from ..webchat_ai_decision_runtime.schemas import AIDecision
from .case_context import CaseContext
from .controlled_action_executor import ActionExecutionResult
from .tool_execution_service import GovernedToolExecutionOptions, execute_controlled_tool_calls, runtime_tool_actions_from_tool_calls


class OSRToolExecutionMode(StrEnum):
    OBSERVE_ONLY = "observe_only"
    POLICY_EXECUTE = "policy_execute"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class OSRToolExecutionFacadeResult:
    mode: OSRToolExecutionMode
    results: tuple[ActionExecutionResult, ...] = field(default_factory=tuple)
    safe_customer_visible_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def executed(self) -> bool:
        return any(item.ok and item.status == "executed" for item in self.results)

    @property
    def blocked(self) -> bool:
        return self.mode == OSRToolExecutionMode.BLOCKED

    def safe_summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "executed": self.executed,
            "results": [
                {
                    "tool_name": item.tool_name,
                    "status": item.status,
                    "ok": item.ok,
                    "error_code": item.error_code,
                }
                for item in self.results
            ],
            "customer_visible_results": list(self.safe_customer_visible_results),
        }


class OSRToolExecutionFacade:
    """Only supported entry point for channel-facing OSR tool execution.

    WebChat/WhatsApp/Voice callers should depend on this facade rather than
    calling `execute_controlled_tool_calls()` directly. The facade returns safe
    customer-visible result templates only. It never sends or enqueues customer
    messages and never accepts provider-native tool calls.
    """

    def __init__(self, db: Session):
        self._db = db

    def execute(
        self,
        *,
        tool_calls: Iterable[Any],
        case_context: CaseContext,
        channel: str | None = None,
        country_code: str | None = None,
        tenant_id: str = "default",
        conversation: WebchatConversation | None = None,
        ticket: Ticket | None = None,
        customer: Customer | None = None,
        ai_decision: AIDecision | None = None,
        mode: OSRToolExecutionMode | str = OSRToolExecutionMode.POLICY_EXECUTE,
        options: GovernedToolExecutionOptions | None = None,
    ) -> OSRToolExecutionFacadeResult:
        normalized_mode = _normalize_mode(mode)
        if normalized_mode == OSRToolExecutionMode.OBSERVE_ONLY:
            return self.observe_only(tool_calls=tool_calls)
        if normalized_mode == OSRToolExecutionMode.BLOCKED:
            return OSRToolExecutionFacadeResult(mode=OSRToolExecutionMode.BLOCKED)
        if normalized_mode == OSRToolExecutionMode.CONFIRMATION_REQUIRED:
            return self.confirmation_required(tool_calls=tool_calls)

        results = tuple(execute_controlled_tool_calls(
            self._db,
            tool_calls=tool_calls,
            case_context=case_context,
            channel=channel,
            country_code=country_code,
            tenant_id=tenant_id,
            conversation=conversation,
            ticket=ticket,
            customer=customer,
            ai_decision=ai_decision,
            options=options,
        ))
        return OSRToolExecutionFacadeResult(
            mode=_mode_from_results(results),
            results=results,
            safe_customer_visible_results=_safe_customer_visible_results(results),
        )

    def observe_only(self, *, tool_calls: Iterable[Any]) -> OSRToolExecutionFacadeResult:
        actions = runtime_tool_actions_from_tool_calls(tool_calls)
        results = tuple(
            ActionExecutionResult(
                ok=False,
                tool_name=action.tool_name,
                status=OSRToolExecutionMode.OBSERVE_ONLY.value,
                summary={"observed": True},
            )
            for action in actions
        )
        return OSRToolExecutionFacadeResult(
            mode=OSRToolExecutionMode.OBSERVE_ONLY,
            results=results,
            safe_customer_visible_results=_safe_customer_visible_results(results),
        )

    def confirmation_required(self, *, tool_calls: Iterable[Any]) -> OSRToolExecutionFacadeResult:
        actions = runtime_tool_actions_from_tool_calls(tool_calls)
        results = tuple(
            ActionExecutionResult(
                ok=False,
                tool_name=action.tool_name,
                status=OSRToolExecutionMode.CONFIRMATION_REQUIRED.value,
                summary={"requires_confirmation": True},
                error_code="confirmation_required",
                error_message="Confirmation is required before this tool can execute.",
            )
            for action in actions
        )
        return OSRToolExecutionFacadeResult(
            mode=OSRToolExecutionMode.CONFIRMATION_REQUIRED,
            results=results,
            safe_customer_visible_results=_safe_customer_visible_results(results),
        )


def _normalize_mode(value: OSRToolExecutionMode | str) -> OSRToolExecutionMode:
    if isinstance(value, OSRToolExecutionMode):
        return value
    try:
        return OSRToolExecutionMode(str(value))
    except ValueError:
        return OSRToolExecutionMode.POLICY_EXECUTE


def _mode_from_results(results: tuple[ActionExecutionResult, ...]) -> OSRToolExecutionMode:
    if not results:
        return OSRToolExecutionMode.OBSERVE_ONLY
    if any(item.status == "confirmation_required" for item in results):
        return OSRToolExecutionMode.CONFIRMATION_REQUIRED
    if any((not item.ok) or item.status in {"blocked", "failed"} for item in results):
        return OSRToolExecutionMode.BLOCKED
    return OSRToolExecutionMode.POLICY_EXECUTE


def _safe_customer_visible_results(results: tuple[ActionExecutionResult, ...]) -> tuple[dict[str, Any], ...]:
    safe: list[dict[str, Any]] = []
    for item in results:
        if not item.customer_visible_summary:
            continue
        safe.append({
            "tool_name": item.tool_name,
            "status": item.status,
            "summary_template": item.customer_visible_summary,
            "send_directly": False,
        })
    return tuple(safe)
