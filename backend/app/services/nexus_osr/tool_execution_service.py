"""Public governed tool-execution authority.

The established executor remains in the private core. This module binds the
production handlers and adds conversation-aware support availability without
forking the executor, policy, audit, or idempotency paths.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...models import Customer, Ticket
from ...models_agent_routing import ConversationControl
from ...webchat_models import WebchatConversation, WebchatHandoffRequest
from ..agent_availability_service import availability_summary
from . import tool_execution_service_core as _core
from .controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionHandler,
)
from .tool_execution_service_core import *  # noqa: F401,F403


_ORIGINAL_PRODUCTION_HANDLERS = _core._production_handlers


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
    handlers = _ORIGINAL_PRODUCTION_HANDLERS(
        db,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
    )

    def support_availability(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        current = _core._resolve_conversation(
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


# The core executor resolves these globals when an action executes. Rebinding
# preserves one execution pipeline while making availability conversation-aware.
_core._production_handlers = _production_handlers
_core._availability_customer_summary = _availability_customer_summary


def __getattr__(name: str):
    return getattr(_core, name)
