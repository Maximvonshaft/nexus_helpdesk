from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ...models import Customer, Ticket
from ...webchat_models import WebchatConversation
from ..nexus_osr.case_context import CaseContext
from ..nexus_osr.tool_execution_service import (
    GovernedToolExecutionOptions,
    execute_controlled_tool_calls,
    executable_tool_names as core_executable_tool_names,
)
from ..webchat_ai_decision_runtime.schemas import AIDecisionToolCall


@dataclass(frozen=True)
class AgentExecutionContext:
    tenant_key: str
    channel_key: str
    session_id: str
    request_id: str
    customer_message: str
    market_id: int | None = None
    language: str | None = None
    conversation_id: int | None = None
    ticket_id: int | None = None
    customer_id: int | None = None
    country_code: str | None = None
    ai_turn_id: int | None = None
    allowed_tools: frozenset[str] = frozenset()
    granted_permissions: frozenset[str] = frozenset()
    actor_capabilities: frozenset[str] = frozenset()
    customer_confirmation_granted: bool = False
    human_confirmation_granted: bool = False


@dataclass(frozen=True)
class ToolObservation:
    tool_name: str
    ok: bool
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    elapsed_ms: int = 0

    def prompt_projection(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "ok": self.ok,
            "status": self.status,
            "result": self.result,
            "error_code": self.error_code,
        }


def executable_tool_names() -> tuple[str, ...]:
    return core_executable_tool_names()


def execute_agent_tool_calls(
    db: Session,
    *,
    calls: list[AIDecisionToolCall],
    context: AgentExecutionContext,
    allow_high_risk_writes: bool = False,
) -> list[ToolObservation]:
    case_context = CaseContext(
        conversation_id=context.conversation_id,
        ticket_id=context.ticket_id,
        channel=context.channel_key,
        country_code=context.country_code,
    ).with_inbound_message(
        context.customer_message,
        channel=context.channel_key,
        country_code=context.country_code,
    )
    conversation = (
        db.get(WebchatConversation, context.conversation_id)
        if context.conversation_id is not None
        else None
    )
    ticket = (
        db.get(Ticket, context.ticket_id)
        if context.ticket_id is not None
        else None
    )
    customer = (
        db.get(Customer, context.customer_id)
        if context.customer_id is not None
        else None
    )
    results = execute_controlled_tool_calls(
        db,
        tool_calls=calls,
        case_context=case_context,
        channel=context.channel_key,
        country_code=context.country_code,
        tenant_id=context.tenant_key,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
        options=GovernedToolExecutionOptions(
            allow_high_risk_write_execution=allow_high_risk_writes,
            allowed_high_risk_write_tools=(
                frozenset(context.allowed_tools)
                if allow_high_risk_writes
                else frozenset()
            ),
            customer_confirmation_granted=(
                context.customer_confirmation_granted
            ),
            human_confirmation_granted=context.human_confirmation_granted,
            allowed_tool_names=frozenset(context.allowed_tools),
            granted_permissions=frozenset(context.granted_permissions),
        ),
    )
    return [
        ToolObservation(
            tool_name=result.tool_name,
            ok=result.ok,
            status=result.status,
            result=dict(result.summary or {}),
            error_code=result.error_code,
        )
        for result in results
    ]
