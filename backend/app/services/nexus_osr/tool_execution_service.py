"""Public governed tool-execution authority.

The private core remains the only executor, policy, audit, idempotency and
handler-registry implementation. This module supplies bounded conversation
context and customer-visible projection without mutating the imported core.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ...models import Customer, Ticket
from ...webchat_models import WebchatConversation, WebchatHandoffRequest
from ..agent_availability_service import bind_availability_request
from ..webchat_ai_decision_runtime.schemas import AIDecision
from . import tool_execution_service_core as _core
from .case_context import CaseContext
from .controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionHandler,
)
from .tool_execution_service_core import *  # noqa: F401,F403


GovernedToolExecutionOptions = _core.GovernedToolExecutionOptions
runtime_tool_actions_from_tool_calls = _core.runtime_tool_actions_from_tool_calls


def _current_conversation(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    case_context: CaseContext | None,
) -> WebchatConversation | None:
    if conversation is not None:
        return conversation
    value = getattr(case_context, "conversation_id", None)
    if value is not None and str(value).isdigit():
        return db.get(WebchatConversation, int(value))
    return None


def _current_request(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    case_context: CaseContext | None,
) -> WebchatHandoffRequest | None:
    current = _current_conversation(
        db,
        conversation=conversation,
        case_context=case_context,
    )
    if current is None or current.current_handoff_request_id is None:
        return None
    row = db.get(WebchatHandoffRequest, current.current_handoff_request_id)
    return row if row is not None and row.conversation_id == current.id else None


def _availability_customer_summary(summary: dict[str, Any]) -> str:
    online = int(summary.get("online_agents") or 0)
    available = int(summary.get("available_capacity") or 0)
    queued = int(summary.get("queue_count") or 0)
    raw_position = summary.get("queue_position")
    position = int(raw_position) if isinstance(raw_position, int) else None
    if online <= 0:
        return "No human support agent is currently online."
    if available > 0:
        return (
            "Human support is available with "
            f"{available} open conversation slot(s)."
        )
    if position is not None and position > 0:
        ahead = max(0, position - 1)
        if ahead == 0:
            return (
                "Human support is currently at capacity. "
                "This customer is next in the eligible queue."
            )
        return (
            "Human support is currently at capacity with "
            f"{ahead} conversation(s) ahead of this customer."
        )
    return (
        "Human support is currently at capacity with "
        f"{queued} conversation(s) waiting."
    )


def _project_availability_result(
    result: ActionExecutionResult,
) -> ActionExecutionResult:
    if result.tool_name != "support.availability" or not result.ok:
        return result
    return replace(
        result,
        customer_visible_summary=_availability_customer_summary(result.summary),
    )


def _production_handlers(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    customer: Customer | None,
) -> dict[str, ActionHandler]:
    handlers = _core._production_handlers(
        db,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
    )
    original = handlers.get("support.availability")
    if original is None:
        return handlers

    def support_availability(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        request_row = _current_request(
            db,
            conversation=conversation,
            case_context=request.case_context,
        )
        with bind_availability_request(request_row):
            return _project_availability_result(original(request))

    handlers["support.availability"] = support_availability
    return handlers


def execute_controlled_tool_calls(
    db: Session,
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
    options: GovernedToolExecutionOptions | None = None,
) -> list[ActionExecutionResult]:
    request_row = _current_request(
        db,
        conversation=conversation,
        case_context=case_context,
    )
    with bind_availability_request(request_row):
        results = _core.execute_controlled_tool_calls(
            db,
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
        )
    return [_project_availability_result(item) for item in results]


def __getattr__(name: str):
    return getattr(_core, name)
