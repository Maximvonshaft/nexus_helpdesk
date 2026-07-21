from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ...models import Customer, Ticket
from ...webchat_models import WebchatConversation
from ..agent_tool_handlers import (
    build_agent_tool_handlers,
    extension_executable_tool_names,
)
from ..nexus_osr.case_context import CaseContext
from ..nexus_osr.tool_execution_service import (
    GovernedToolExecutionOptions,
    execute_controlled_tool_calls,
    executable_tool_names as core_executable_tool_names,
)
from ..webchat_ai_decision_runtime.schemas import AIDecisionToolCall
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
    with bind_agent_release_snapshot(context.release_snapshot):
        with bind_agent_tool_handlers(handlers):
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
                    customer_confirmation_granted=context.customer_confirmation_granted,
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
            elapsed_ms=max(0, int(result.elapsed_ms or 0)),
        )
        for result in results
    ]
