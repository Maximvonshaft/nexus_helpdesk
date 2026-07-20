"""Public governed tool-execution authority.

The private core owns the single executor, handler registry, policy, audit and
idempotency implementation. This module is the stable public import path and
binds bounded conversation context before delegating to that single core.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ...models import Customer, Ticket
from ...models_agent_routing import ConversationControl
from ...webchat_models import WebchatConversation, WebchatHandoffRequest
from ..agent_availability_service import (
    availability_summary,
    bind_availability_conversation,
)
from ..webchat_ai_decision_runtime.schemas import AIDecision
from . import tool_execution_service_core as _core
from .case_context import CaseContext
from .controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionHandler,
)
from .runtime_decision_contract import RuntimeToolAction
from .tool_execution_service_core import *  # noqa: F401,F403


_production_handlers_core = _core._production_handlers


def _resolve_conversation(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    case_context: CaseContext | None,
) -> WebchatConversation | None:
    if conversation is not None:
        return conversation
    if (
        case_context is not None
        and case_context.conversation_id is not None
        and str(case_context.conversation_id).isdigit()
    ):
        return db.get(WebchatConversation, int(case_context.conversation_id))
    return None


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


def _production_handlers(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    customer: Customer | None,
) -> dict[str, ActionHandler]:
    handlers = _production_handlers_core(
        db,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
    )

    def support_availability(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        current = _resolve_conversation(
            db,
            conversation=conversation,
            case_context=request.case_context,
        )
        if current is None:
            return ActionExecutionResult(
                False,
                request.action.tool_name,
                "failed",
                error_code="conversation_required",
            )
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == current.id)
            .first()
        )
        if control is None:
            return ActionExecutionResult(
                False,
                request.action.tool_name,
                "failed",
                error_code="conversation_control_required",
            )
        request_row = (
            db.get(WebchatHandoffRequest, current.current_handoff_request_id)
            if current.current_handoff_request_id is not None
            else None
        )
        if request_row is not None and request_row.conversation_id != current.id:
            request_row = None
        summary = availability_summary(
            db,
            tenant_key=control.tenant_key,
            country_code=control.country_code,
            channel_key=control.channel_key,
            request_row=request_row,
        )
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary=summary,
            customer_visible_summary=_availability_customer_summary(summary),
            case_context=request.case_context,
        )

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
    current = _resolve_conversation(
        db,
        conversation=conversation,
        case_context=case_context,
    )
    with bind_availability_conversation(
        current.id if current is not None else None
    ):
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
    return [
        replace(
            result,
            customer_visible_summary=_availability_customer_summary(result.summary),
        )
        if result.ok and result.tool_name == "support.availability"
        else result
        for result in results
    ]


def __getattr__(name: str):
    return getattr(_core, name)
