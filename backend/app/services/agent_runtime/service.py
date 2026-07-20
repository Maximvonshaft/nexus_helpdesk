from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...db import SessionLocal
from ..ai_runtime.schemas import RuntimeAIProviderRequest, RuntimeAIProviderResult
from ..provider_runtime.output_contracts import WEBCHAT_RUNTIME_OUTPUT_CONTRACT
from ..provider_runtime.router import ProviderRuntimeRouter
from ..provider_runtime.schemas import ProviderRequest
from ..webchat_ai_decision_runtime.schemas import AIDecision
from ..webchat_ai_decision_runtime.tool_registry import get_tool_contract, prompt_tool_catalog
from .skill_registry import prompt_skill_catalog
from .tool_executor import (
    AgentExecutionContext,
    ToolObservation,
    executable_tool_names,
    execute_agent_tool_calls,
)


@dataclass(frozen=True)
class AgentRoundTrace:
    round_index: int
    next_action: str | None
    tool_names: tuple[str, ...] = ()
    observation_statuses: tuple[str, ...] = ()
    provider: str | None = None
    elapsed_ms: int = 0
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "round_index": self.round_index,
            "next_action": self.next_action,
            "tool_names": list(self.tool_names),
            "observation_statuses": list(self.observation_statuses),
            "provider": self.provider,
            "elapsed_ms": self.elapsed_ms,
            "error_code": self.error_code,
        }


@dataclass
class AgentRunState:
    observations: list[ToolObservation] = field(default_factory=list)
    traces: list[AgentRoundTrace] = field(default_factory=list)
    executed_calls: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0


async def run_agent(request: RuntimeAIProviderRequest) -> RuntimeAIProviderResult:
    """Execute the canonical model → Tool → observation → model loop."""

    started = time.monotonic()
    db = SessionLocal()
    try:
        return await _run_agent_with_db(db, request=request, started=started)
    finally:
        db.close()


async def _run_agent_with_db(
    db: Session,
    *,
    request: RuntimeAIProviderRequest,
    started: float,
) -> RuntimeAIProviderResult:
    metadata = dict(request.metadata or {})
    state = AgentRunState()
    max_rounds = _int_env("NEXUS_AGENT_MAX_TOOL_ROUNDS", 3, minimum=1, maximum=6)
    allow_high_risk_writes = _env_bool("NEXUS_AGENT_HIGH_RISK_WRITES_ENABLED", False)
    available_tools = _available_tools(metadata, allow_high_risk_writes=allow_high_risk_writes)
    execution_context = _execution_context(request, available_tools=available_tools)
    skills = prompt_skill_catalog(available_tools=available_tools)
    tools = prompt_tool_catalog(names=sorted(available_tools))

    for round_index in range(max_rounds + 1):
        round_metadata = {
            **metadata,
            "agent_runtime_version": "nexus.agent_runtime.v1",
            "agent_round": round_index,
            "agent_skills": skills,
            "agent_tools": tools,
            "tool_observations": [item.prompt_projection() for item in state.observations],
            "customer_language": request.language or metadata.get("customer_language") or metadata.get("language"),
        }
        provider_request = ProviderRequest(
            request_id=_round_request_id(request, round_index),
            tenant_id=request.tenant_key,
            tenant_key=request.tenant_key,
            channel_key=request.channel_key,
            session_id=request.session_id,
            scenario="agent_turn",
            body=request.body,
            recent_context=request.recent_context,
            output_contract=WEBCHAT_RUNTIME_OUTPUT_CONTRACT,
            timeout_ms=15000,
            metadata=round_metadata,
        )
        result = await ProviderRuntimeRouter(db).route(provider_request)
        state.elapsed_ms = _elapsed(started)
        if not result.ok or not result.structured_output:
            state.traces.append(
                AgentRoundTrace(
                    round_index=round_index,
                    next_action=None,
                    provider=result.provider,
                    elapsed_ms=result.elapsed_ms,
                    error_code=result.error_code or "provider_unavailable",
                )
            )
            return _fallback_result(
                request,
                state=state,
                error_code=result.error_code or "provider_unavailable",
                elapsed_ms=state.elapsed_ms,
            )

        if not _authoritative_provider_audit_exists(
            db,
            request=provider_request,
            provider=result.provider,
        ):
            state.traces.append(
                AgentRoundTrace(
                    round_index=round_index,
                    next_action=None,
                    provider=result.provider,
                    elapsed_ms=result.elapsed_ms,
                    error_code="provider_runtime_audit_unavailable",
                )
            )
            return _fallback_result(
                request,
                state=state,
                error_code="provider_runtime_audit_unavailable",
                elapsed_ms=state.elapsed_ms,
            )

        try:
            decision = AIDecision.model_validate(result.structured_output)
        except Exception as exc:
            state.traces.append(
                AgentRoundTrace(
                    round_index=round_index,
                    next_action=None,
                    provider=result.provider,
                    elapsed_ms=result.elapsed_ms,
                    error_code=f"invalid_agent_turn:{type(exc).__name__}",
                )
            )
            return _fallback_result(
                request,
                state=state,
                error_code="invalid_agent_turn",
                elapsed_ms=state.elapsed_ms,
            )

        if decision.next_action != "call_tool":
            state.traces.append(
                AgentRoundTrace(
                    round_index=round_index,
                    next_action=decision.next_action,
                    provider=result.provider,
                    elapsed_ms=result.elapsed_ms,
                )
            )
            return RuntimeAIProviderResult(
                ok=True,
                ai_generated=True,
                reply_source=result.provider,
                raw_provider=result.raw_provider or result.provider,
                raw_payload_safe_summary=_safe_summary(
                    state,
                    decision=decision,
                    provider_summary=result.raw_payload_safe_summary,
                ),
                reply=decision.customer_reply,
                intent=decision.intent,
                handoff_required=decision.handoff_required,
                handoff_reason=decision.handoff_reason,
                recommended_agent_action=(
                    "Review the conversation and take over." if decision.handoff_required else None
                ),
                tool_calls=list(state.executed_calls),
                elapsed_ms=state.elapsed_ms,
                error_code=None,
                retry_after_ms=None,
            )

        if round_index >= max_rounds:
            state.traces.append(
                AgentRoundTrace(
                    round_index=round_index,
                    next_action="call_tool",
                    tool_names=tuple(call.tool_name for call in decision.tool_calls),
                    provider=result.provider,
                    elapsed_ms=result.elapsed_ms,
                    error_code="max_tool_rounds_exceeded",
                )
            )
            return _fallback_result(
                request,
                state=state,
                error_code="max_tool_rounds_exceeded",
                elapsed_ms=state.elapsed_ms,
            )

        observations = execute_agent_tool_calls(
            db,
            calls=decision.tool_calls,
            context=execution_context,
            allow_high_risk_writes=allow_high_risk_writes,
        )
        try:
            db.commit()
        except Exception:
            db.rollback()
        state.executed_calls.extend(
            {
                "round": round_index,
                "tool_name": call.tool_name,
                "status": observation.status,
                "ok": observation.ok,
                "error_code": observation.error_code,
            }
            for call, observation in zip(decision.tool_calls, observations)
        )
        state.traces.append(
            AgentRoundTrace(
                round_index=round_index,
                next_action="call_tool",
                tool_names=tuple(call.tool_name for call in decision.tool_calls),
                observation_statuses=tuple(item.status for item in observations),
                provider=result.provider,
                elapsed_ms=result.elapsed_ms,
            )
        )
        state.observations.extend(observations)

    return _fallback_result(
        request,
        state=state,
        error_code="agent_loop_exhausted",
        elapsed_ms=_elapsed(started),
    )


def _authoritative_provider_audit_exists(
    db: Session,
    *,
    request: ProviderRequest,
    provider: str | None,
) -> bool:
    """A Provider result is authoritative only after its durable success audit exists."""

    try:
        row = db.execute(
            text(
                """
                SELECT 1
                FROM provider_runtime_audit_logs
                WHERE request_id = :request_id
                  AND tenant_id = :tenant_id
                  AND channel_key = :channel_key
                  AND session_id = :session_id
                  AND provider = :provider
                  AND operation = 'generate'
                  AND status = 'ok'
                  AND error_code IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "channel_key": request.channel_key,
                "session_id": request.session_id,
                "provider": provider,
            },
        ).first()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False
    return row is not None


def _execution_context(
    request: RuntimeAIProviderRequest,
    *,
    available_tools: set[str],
) -> AgentExecutionContext:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    execution = metadata.get("agent_execution_context") if isinstance(metadata.get("agent_execution_context"), dict) else {}
    permissions = {
        permission
        for name in available_tools
        if (contract := get_tool_contract(name)) is not None
        for permission in contract.required_permissions
    }
    capabilities = {
        f"tool:{name}:write"
        for name in available_tools
        if (contract := get_tool_contract(name)) is not None and contract.is_write_tool
    }
    return AgentExecutionContext(
        tenant_key=request.tenant_key,
        channel_key=request.channel_key,
        session_id=request.session_id,
        request_id=request.request_id or f"agent-{request.session_id}",
        customer_message=request.body,
        market_id=request.market_id,
        language=request.language,
        conversation_id=_optional_int(execution.get("conversation_id")),
        ticket_id=_optional_int(execution.get("ticket_id")),
        customer_id=_optional_int(execution.get("customer_id")),
        country_code=str(execution.get("country_code") or "").strip()[:8] or None,
        ai_turn_id=_optional_int(execution.get("ai_turn_id")),
        allowed_tools=frozenset(available_tools),
        granted_permissions=frozenset(permissions),
        actor_capabilities=frozenset(capabilities),
    )


def _available_tools(metadata: dict[str, Any], *, allow_high_risk_writes: bool) -> set[str]:
    executable = set(executable_tool_names())
    configured = metadata.get("agent_allowed_tools")
    if isinstance(configured, (list, tuple, set)):
        executable &= {str(item).strip() for item in configured if str(item).strip()}
    if not allow_high_risk_writes:
        executable = {
            name
            for name in executable
            if not (
                (contract := get_tool_contract(name)) is not None
                and contract.is_write_tool
                and contract.risk_level == "high"
            )
        }
    return executable


def _fallback_result(
    request: RuntimeAIProviderRequest,
    *,
    state: AgentRunState,
    error_code: str,
    elapsed_ms: int,
) -> RuntimeAIProviderResult:
    reply = _localized_fallback(request.language, request.body)
    return RuntimeAIProviderResult(
        ok=True,
        ai_generated=False,
        reply_source="agent_runtime:fallback",
        raw_provider="agent_runtime",
        raw_payload_safe_summary=_safe_summary(state, error_code=error_code),
        reply=reply,
        intent="runtime_unavailable",
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        tool_calls=list(state.executed_calls),
        elapsed_ms=elapsed_ms,
        error_code=error_code,
        retry_after_ms=1500,
    )


def _localized_fallback(language: str | None, body: str) -> str:
    hint = str(language or "").strip().lower()
    if hint == "zh" or any("\u4e00" <= char <= "\u9fff" for char in body):
        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"
    if hint == "de":
        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."
    return "Sorry, I could not complete that request right now. Please try again or ask for human support."


def _safe_summary(
    state: AgentRunState,
    *,
    decision: AIDecision | None = None,
    provider_summary: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "agent_runtime": True,
        "agent_runtime_version": "nexus.agent_runtime.v1",
        "round_count": len(state.traces),
        "rounds": [item.as_dict() for item in state.traces[:8]],
        "executed_tools": list(state.executed_calls[:20]),
        "elapsed_ms": state.elapsed_ms,
    }
    if decision is not None:
        summary["ai_decision"] = decision.safe_public_summary()
    if provider_summary:
        summary["provider"] = provider_summary
    if error_code:
        summary["error_code"] = error_code[:160]
    return summary


def _round_request_id(request: RuntimeAIProviderRequest, round_index: int) -> str:
    base = str(request.request_id or f"agent-{request.session_id}").strip()[:130]
    return f"{base}:round:{round_index}"[:160]


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _elapsed(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
