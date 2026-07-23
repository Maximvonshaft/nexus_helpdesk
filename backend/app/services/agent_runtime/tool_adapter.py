from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ...models import Customer, Ticket
from ...models_agent_runtime import AgentToolConfirmation
from ...webchat_models import WebchatConversation, WebchatMessage
from ..agent_confirmation_service import (
    confirmation_projection,
    consume_confirmation,
    create_or_reuse_confirmation,
    expire_confirmation_if_needed,
    tool_arguments_sha256,
)
from ..agent_tool_handlers import (
    build_agent_tool_handlers,
    extension_executable_tool_names,
)
from ..nexus_osr.case_context import CaseContext
from ..nexus_osr.controlled_action_executor import ActionExecutionResult
from ..nexus_osr.tool_execution_service import (
    GovernedToolExecutionOptions,
    execute_controlled_tool_calls,
    executable_tool_names as core_executable_tool_names,
)
from ..webchat_ai_decision_runtime.schemas import AIDecisionToolCall
from ..webchat_ai_decision_runtime.tool_registry import get_tool_contract
from .execution_scope import bind_agent_release_snapshot, bind_agent_tool_handlers


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
    customer_confirmation_id: str | None = None
    customer_confirmation_tool_name: str | None = None
    customer_confirmation_arguments_sha256: str | None = None
    human_confirmation_granted: bool = False
    release_snapshot: dict[str, Any] | None = None


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
            "elapsed_ms": self.elapsed_ms,
        }


def executable_tool_names() -> tuple[str, ...]:
    return tuple(
        sorted(
            set(core_executable_tool_names())
            | set(extension_executable_tool_names())
        )
    )


def execute_agent_tool_calls(
    db: Session,
    *,
    calls: list[AIDecisionToolCall],
    context: AgentExecutionContext,
    allow_high_risk_writes: bool = False,
) -> list[ToolObservation]:
    """Execute one canonical Tool transaction in the calling worker thread.

    A SQLAlchemy Session is not thread-safe. The worker therefore owns a fresh
    Session bound to the exact same authoritative Engine/Connection pool as the
    caller. It never falls back to a process-global Session factory that could
    point at another test, tenant or deployment database.
    """

    if isinstance(db, Session):
        worker_db = _worker_session(db)
        try:
            observations = _execute_with_db(
                worker_db,
                calls=calls,
                context=context,
                allow_high_risk_writes=allow_high_risk_writes,
            )
            worker_db.commit()
            return observations
        except Exception:
            try:
                worker_db.rollback()
            except Exception:
                pass
            raise
        finally:
            worker_db.close()
    return _execute_with_db(
        db,
        calls=calls,
        context=context,
        allow_high_risk_writes=allow_high_risk_writes,
    )


def _worker_session(db: Session) -> Session:
    bind = db.get_bind()
    if bind is None:
        raise RuntimeError("agent_tool_worker_database_bind_unavailable")
    factory = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    return factory()


def _execution_options(
    *,
    context: AgentExecutionContext,
    allow_high_risk_writes: bool,
    customer_confirmation_granted: bool,
) -> GovernedToolExecutionOptions:
    return GovernedToolExecutionOptions(
        allow_high_risk_write_execution=allow_high_risk_writes,
        allowed_high_risk_write_tools=(
            frozenset(context.allowed_tools)
            if allow_high_risk_writes
            else frozenset()
        ),
        customer_confirmation_granted=customer_confirmation_granted,
        human_confirmation_granted=context.human_confirmation_granted,
        allowed_tool_names=frozenset(context.allowed_tools),
        granted_permissions=frozenset(context.granted_permissions),
    )


def _active_exact_confirmation(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    call: AIDecisionToolCall,
) -> AgentToolConfirmation | None:
    if conversation is None:
        return None
    query = db.query(AgentToolConfirmation).filter(
        AgentToolConfirmation.conversation_id == conversation.id,
        AgentToolConfirmation.status == "confirmed",
        AgentToolConfirmation.tool_name == call.tool_name,
        AgentToolConfirmation.arguments_sha256
        == tool_arguments_sha256(call.arguments),
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    row = query.order_by(AgentToolConfirmation.id.desc()).first()
    if row is None:
        return None
    if expire_confirmation_if_needed(row):
        db.flush()
        return None
    if row.tenant_key != conversation.tenant_key:
        return None
    return row


def _latest_visitor_message_id(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> int | None:
    row = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "visitor",
        )
        .order_by(WebchatMessage.id.desc())
        .first()
    )
    return int(row[0]) if row else None


def _confirmation_result(
    result: ActionExecutionResult,
    *,
    confirmation: AgentToolConfirmation,
) -> ActionExecutionResult:
    projection = confirmation_projection(confirmation)
    return ActionExecutionResult(
        ok=False,
        tool_name=result.tool_name,
        status="confirmation_required",
        summary={
            **dict(result.summary or {}),
            "customer_confirmation_required": True,
            "confirmation": projection,
        },
        customer_visible_summary=confirmation.question_text,
        case_context=result.case_context,
        policy_decision=result.policy_decision,
        error_code="customer_confirmation_required",
        error_message="Customer confirmation is required before this Tool can execute.",
        elapsed_ms=result.elapsed_ms,
    )


def _execute_one(
    db: Session,
    *,
    call: AIDecisionToolCall,
    case_context: CaseContext,
    context: AgentExecutionContext,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    customer: Customer | None,
    allow_high_risk_writes: bool,
) -> ActionExecutionResult:
    contract = get_tool_contract(call.tool_name)
    exact_confirmation = (
        _active_exact_confirmation(db, conversation=conversation, call=call)
        if contract is not None and contract.confirmation_required
        else None
    )
    granted = bool(exact_confirmation)
    if conversation is None and context.customer_confirmation_granted:
        # Preserve non-conversation administrative execution semantics. Public
        # Conversation paths never trust a model-supplied boolean.
        granted = True
    results = execute_controlled_tool_calls(
        db,
        tool_calls=[call],
        case_context=case_context,
        channel=context.channel_key,
        country_code=context.country_code,
        tenant_id=context.tenant_key,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
        options=_execution_options(
            context=context,
            allow_high_risk_writes=allow_high_risk_writes,
            customer_confirmation_granted=granted,
        ),
    )
    if not results:
        return ActionExecutionResult(
            ok=False,
            tool_name=call.tool_name,
            status="failed",
            error_code="tool_execution_result_missing",
            error_message="Canonical Tool executor returned no result.",
        )
    result = results[0]
    if (
        conversation is not None
        and result.status == "confirmation_required"
        and result.error_code == "customer_confirmation_required"
    ):
        confirmation = create_or_reuse_confirmation(
            db,
            conversation=conversation,
            tool_name=call.tool_name,
            arguments=dict(call.arguments or {}),
            requested_message_id=_latest_visitor_message_id(
                db,
                conversation=conversation,
            ),
        )
        return _confirmation_result(result, confirmation=confirmation)
    if result.ok and exact_confirmation is not None:
        consume_confirmation(
            db,
            row=exact_confirmation,
            tool_call_log_id=None,
        )
    return result


def _execute_with_db(
    db: Session,
    *,
    calls: list[AIDecisionToolCall],
    context: AgentExecutionContext,
    allow_high_risk_writes: bool,
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
    ticket = db.get(Ticket, context.ticket_id) if context.ticket_id is not None else None
    customer = (
        db.get(Customer, context.customer_id)
        if context.customer_id is not None
        else None
    )
    handlers = build_agent_tool_handlers(
        db,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
    )
    results: list[ActionExecutionResult] = []
    with bind_agent_release_snapshot(context.release_snapshot):
        with bind_agent_tool_handlers(handlers):
            for call in calls:
                results.append(
                    _execute_one(
                        db,
                        call=call,
                        case_context=case_context,
                        context=context,
                        conversation=conversation,
                        ticket=ticket,
                        customer=customer,
                        allow_high_risk_writes=allow_high_risk_writes,
                    )
                )
    return [
        ToolObservation(
            tool_name=result.tool_name,
            ok=result.ok,
            status=result.status,
            result=dict(result.summary or {}),
            error_code=result.error_code,
            elapsed_ms=max(0, int(result.elapsed_ms or 0)),
        )
        for result in results
    ]
