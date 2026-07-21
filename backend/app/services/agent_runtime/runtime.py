from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...db import SessionLocal
from ..agent_control_config import RUNTIME_POLICY
from ..agent_release_service import record_run_snapshot, resolve_agent_release
from ..agent_tool_contracts import bootstrap_agent_tool_contracts
from ..ai_runtime.schemas import RuntimeAIProviderRequest, RuntimeAIProviderResult
from ..provider_runtime.output_contracts import AGENT_TURN_OUTPUT_CONTRACT
from ..provider_runtime.router import ProviderRuntimeRouter
from ..provider_runtime.schemas import ProviderRequest
from ..webchat_ai_decision_runtime.schemas import AIDecision
from ..webchat_ai_decision_runtime.tool_registry import get_tool_contract, prompt_tool_catalog
from .playbook_registry import prompt_playbook_catalog
from .terminal_reply import customer_visible_fallback
from .tool_adapter import (
    AgentExecutionContext,
    ToolObservation,
    executable_tool_names,
    execute_agent_tool_calls,
)

bootstrap_agent_tool_contracts()


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
    """Execute the one canonical model → Tool → observation → model loop."""

    started = time.monotonic()
    db = SessionLocal()
    try:
        result = await run_agent_with_db(db, request=request, started=started)
        db.commit()
        return result
    except Exception:
        _safe_rollback(db)
        raise
    finally:
        db.close()


async def run_agent_with_db(
    db: Session,
    *,
    request: RuntimeAIProviderRequest,
    started: float | None = None,
) -> RuntimeAIProviderResult:
    started = started if started is not None else time.monotonic()
    metadata = dict(request.metadata or {})
    state = AgentRunState()
    run_request_id = str(
        request.request_id or f"agent-{request.session_id}-{time.time_ns()}"
    )[:160]

    try:
        resolved_release = resolve_agent_release(
            db,
            tenant_key=request.tenant_key,
            environment=str(metadata.get("agent_environment") or "production"),
            market_id=request.market_id,
            channel=request.channel_key,
            language=request.language,
            case_type=_optional_text(
                (metadata.get("channel_context") or {}).get("case_type")
                if isinstance(metadata.get("channel_context"), dict)
                else None
            ),
            cohort_key=request.session_id,
        )
        supplied_digest = _optional_text(metadata.get("agent_release_digest"))
        if supplied_digest and supplied_digest != resolved_release.digest:
            raise RuntimeError("agent_release_context_mismatch")
        record_run_snapshot(
            db,
            request_id=run_request_id,
            session_id=request.session_id,
            tenant_key=request.tenant_key,
            resolved=resolved_release,
        )
        release_snapshot = resolved_release.snapshot
        metadata["agent_release_snapshot"] = release_snapshot
        metadata["agent_release_digest"] = resolved_release.digest
    except Exception as exc:
        state.elapsed_ms = _elapsed(started)
        state.traces.append(
            AgentRoundTrace(
                round_index=0,
                next_action=None,
                error_code=f"agent_release_resolution_failed:{type(exc).__name__}",
            )
        )
        return _fallback_result(
            request,
            state=state,
            error_code="agent_release_resolution_failed",
            elapsed_ms=state.elapsed_ms,
        )

    try:
        policy = _runtime_policy(db, request, release_snapshot=release_snapshot)
        release_playbooks = prompt_playbook_catalog(
            db,
            market_id=request.market_id,
            channel=request.channel_key,
            language=request.language,
            release_snapshot=release_snapshot,
        )
    except Exception as exc:
        state.elapsed_ms = _elapsed(started)
        state.traces.append(
            AgentRoundTrace(
                round_index=0,
                next_action=None,
                error_code=f"agent_release_contract_failed:{type(exc).__name__}",
            )
        )
        return _fallback_result(
            request,
            state=state,
            error_code="agent_release_contract_failed",
            elapsed_ms=state.elapsed_ms,
            release_snapshot=release_snapshot,
        )

    hard_round_ceiling = _int_env(
        "NEXUS_AGENT_MAX_TOOL_ROUNDS",
        6,
        minimum=1,
        maximum=6,
    )
    max_rounds = min(int(policy.get("max_tool_rounds") or 3), hard_round_ceiling)
    allow_high_risk_writes = bool(policy.get("allow_high_risk_writes")) and _env_bool(
        "NEXUS_AGENT_HIGH_RISK_WRITES_ENABLED",
        False,
    )
    available_tools = _available_tools(
        metadata,
        runtime_policy=policy,
        release_snapshot=release_snapshot,
        playbooks=release_playbooks,
        allow_high_risk_writes=allow_high_risk_writes,
    )
    playbooks = prompt_playbook_catalog(
        db,
        market_id=request.market_id,
        channel=request.channel_key,
        language=request.language,
        available_tools=available_tools,
        release_snapshot=release_snapshot,
    )
    tools = prompt_tool_catalog(names=sorted(available_tools))
    execution_context = _execution_context(
        request,
        available_tools=available_tools,
        release_snapshot=release_snapshot,
    )
    timeout_ms = int(policy.get("provider_timeout_ms") or 15000)

    for round_index in range(max_rounds + 1):
        round_metadata = {
            **metadata,
            "agent_runtime_version": "nexus.agent_runtime.v3",
            "agent_round": round_index,
            "agent_playbooks": playbooks,
            "agent_tools": tools,
            "agent_runtime_policy": {
                "max_tool_rounds": max_rounds,
                "allow_high_risk_writes": allow_high_risk_writes,
                "provider_timeout_ms": timeout_ms,
            },
            "tool_observations": [
                item.prompt_projection() for item in state.observations
            ],
            "customer_language": (
                request.language
                or metadata.get("customer_language")
                or metadata.get("language")
            ),
        }
        provider_request = ProviderRequest(
            request_id=_round_request_id(run_request_id, round_index),
            tenant_id=request.tenant_key,
            tenant_key=request.tenant_key,
            channel_key=request.channel_key,
            session_id=request.session_id,
            scenario="agent_turn",
            body=request.body,
            recent_context=request.recent_context,
            output_contract=AGENT_TURN_OUTPUT_CONTRACT,
            timeout_ms=timeout_ms,
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
                release_snapshot=release_snapshot,
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
                release_snapshot=release_snapshot,
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
                release_snapshot=release_snapshot,
            )

        if decision.next_action != "call_tool":
            handoff_committed = _committed_handoff_observed(state)
            handoff_requested = (
                decision.next_action == "request_handoff"
                or decision.handoff_required
            )
            if handoff_requested and not handoff_committed:
                state.traces.append(
                    AgentRoundTrace(
                        round_index=round_index,
                        next_action=decision.next_action,
                        provider=result.provider,
                        elapsed_ms=result.elapsed_ms,
                        error_code="handoff_tool_side_effect_missing",
                    )
                )
                return _fallback_result(
                    request,
                    state=state,
                    error_code="handoff_tool_side_effect_missing",
                    elapsed_ms=state.elapsed_ms,
                    release_snapshot=release_snapshot,
                )
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
                    playbooks=playbooks,
                    tools=tools,
                    policy=policy,
                    release_snapshot=release_snapshot,
                ),
                reply=decision.customer_reply,
                intent=decision.intent,
                handoff_required=handoff_committed,
                handoff_reason=(
                    decision.handoff_reason or "handoff_requested"
                    if handoff_committed
                    else None
                ),
                recommended_agent_action=(
                    "Review the conversation and take over."
                    if handoff_committed
                    else None
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
                release_snapshot=release_snapshot,
            )

        try:
            observations = execute_agent_tool_calls(
                db,
                calls=decision.tool_calls,
                context=execution_context,
                allow_high_risk_writes=allow_high_risk_writes,
            )
        except Exception:
            _safe_rollback(db)
            observations = _failed_tool_observations(
                decision,
                error_code="tool_execution_failed",
            )
            state.elapsed_ms = _elapsed(started)
            _record_tool_observations(
                state,
                round_index=round_index,
                decision=decision,
                observations=observations,
                provider=result.provider,
                elapsed_ms=result.elapsed_ms,
                error_code="tool_execution_failed",
            )
            return _fallback_result(
                request,
                state=state,
                error_code="tool_execution_failed",
                elapsed_ms=state.elapsed_ms,
                release_snapshot=release_snapshot,
            )
        try:
            db.commit()
        except Exception:
            _safe_rollback(db)
            observations = _failed_tool_observations(
                decision,
                error_code="tool_transaction_commit_failed",
            )
            state.elapsed_ms = _elapsed(started)
            _record_tool_observations(
                state,
                round_index=round_index,
                decision=decision,
                observations=observations,
                provider=result.provider,
                elapsed_ms=result.elapsed_ms,
                error_code="tool_transaction_commit_failed",
            )
            return _fallback_result(
                request,
                state=state,
                error_code="tool_transaction_commit_failed",
                elapsed_ms=state.elapsed_ms,
                release_snapshot=release_snapshot,
            )
        _record_tool_observations(
            state,
            round_index=round_index,
            decision=decision,
            observations=observations,
            provider=result.provider,
            elapsed_ms=result.elapsed_ms,
        )

    return _fallback_result(
        request,
        state=state,
        error_code="agent_loop_exhausted",
        elapsed_ms=_elapsed(started),
        release_snapshot=release_snapshot,
    )


def _runtime_policy(
    db: Session,
    request: RuntimeAIProviderRequest,
    *,
    release_snapshot: dict[str, Any],
) -> dict[str, Any]:
    del db, request
    return _released_resource(release_snapshot, RUNTIME_POLICY)


def _released_resource(
    release_snapshot: dict[str, Any],
    config_type: str,
) -> dict[str, Any]:
    if release_snapshot.get("source") != "deployment":
        raise RuntimeError("agent_release_snapshot_required")
    resolved = release_snapshot.get("resolved")
    resources = resolved.get("resources") if isinstance(resolved, dict) else None
    if not isinstance(resources, list):
        raise RuntimeError("agent_release_resources_invalid")
    matches = [
        item.get("content")
        for item in resources
        if isinstance(item, dict) and item.get("config_type") == config_type
    ]
    if len(matches) != 1 or not isinstance(matches[0], dict):
        raise RuntimeError(f"agent_release_{config_type}_ambiguous")
    return dict(matches[0])


def _committed_handoff_observed(state: AgentRunState) -> bool:
    return any(
        observation.tool_name == "handoff.request.create"
        and observation.ok
        and observation.status in {"executed", "duplicate"}
        for observation in state.observations
    )


def _failed_tool_observations(
    decision: AIDecision,
    *,
    error_code: str,
) -> list[ToolObservation]:
    return [
        ToolObservation(
            tool_name=call.tool_name,
            ok=False,
            status="failed",
            result={},
            error_code=error_code,
        )
        for call in decision.tool_calls
    ]


def _record_tool_observations(
    state: AgentRunState,
    *,
    round_index: int,
    decision: AIDecision,
    observations: list[ToolObservation],
    provider: str | None,
    elapsed_ms: int,
    error_code: str | None = None,
) -> None:
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
            provider=provider,
            elapsed_ms=elapsed_ms,
            error_code=error_code,
        )
    )
    state.observations.extend(observations)


def _safe_rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def _authoritative_provider_audit_exists(
    db: Session,
    *,
    request: ProviderRequest,
    provider: str | None,
) -> bool:
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
        _safe_rollback(db)
        return False
    return row is not None


def _execution_context(
    request: RuntimeAIProviderRequest,
    *,
    available_tools: set[str],
    release_snapshot: dict[str, Any],
) -> AgentExecutionContext:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    execution = (
        metadata.get("agent_execution_context")
        if isinstance(metadata.get("agent_execution_context"), dict)
        else {}
    )
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
        granted_permissions=_string_set(execution.get("granted_permissions")),
        actor_capabilities=_string_set(execution.get("actor_capabilities")),
        customer_confirmation_granted=bool(
            execution.get("customer_confirmation_granted") is True
        ),
        human_confirmation_granted=bool(
            execution.get("human_confirmation_granted") is True
        ),
        release_snapshot=release_snapshot,
    )


def _available_tools(
    metadata: dict[str, Any],
    *,
    runtime_policy: dict[str, Any],
    release_snapshot: dict[str, Any],
    playbooks: list[dict[str, Any]],
    allow_high_risk_writes: bool,
) -> set[str]:
    executable = set(executable_tool_names())
    policy_tools = runtime_policy.get("allowed_tools")
    if isinstance(policy_tools, list) and policy_tools:
        executable &= {
            str(item).strip() for item in policy_tools if str(item).strip()
        }
    configured = metadata.get("agent_allowed_tools")
    if isinstance(configured, (list, tuple, set)):
        executable &= {
            str(item).strip() for item in configured if str(item).strip()
        }
    if release_snapshot.get("source") != "deployment":
        raise RuntimeError("agent_release_snapshot_required")
    playbook_tools = {
        str(name)
        for playbook in playbooks
        for name in (playbook.get("tools") or [])
        if str(name)
    }
    executable &= playbook_tools
    manifest = release_snapshot.get("manifest")
    if not isinstance(manifest, dict) or not manifest.get("integrations"):
        executable -= {"integration.read", "integration.write"}
    if not isinstance(manifest, dict) or not manifest.get("knowledge"):
        executable.discard("knowledge.search")
    execution = (
        metadata.get("agent_execution_context")
        if isinstance(metadata.get("agent_execution_context"), dict)
        else {}
    )
    granted_permissions = _string_set(execution.get("granted_permissions"))
    executable = {
        name
        for name in executable
        if (
            (contract := get_tool_contract(name)) is not None
            and set(contract.required_permissions).issubset(granted_permissions)
        )
    }
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
    release_snapshot: dict[str, Any] | None = None,
) -> RuntimeAIProviderResult:
    reply = customer_visible_fallback(request.language, request.body)
    return RuntimeAIProviderResult(
        ok=True,
        ai_generated=False,
        reply_source="agent_runtime:fallback",
        raw_provider="agent_runtime",
        raw_payload_safe_summary=_safe_summary(
            state,
            error_code=error_code,
            release_snapshot=release_snapshot,
        ),
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


def _safe_summary(
    state: AgentRunState,
    *,
    decision: AIDecision | None = None,
    provider_summary: dict[str, Any] | None = None,
    error_code: str | None = None,
    playbooks: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    release_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    release = (
        release_snapshot.get("release")
        if isinstance(release_snapshot, dict)
        and isinstance(release_snapshot.get("release"), dict)
        else {}
    )
    deployment = (
        release_snapshot.get("deployment")
        if isinstance(release_snapshot, dict)
        and isinstance(release_snapshot.get("deployment"), dict)
        else {}
    )
    summary: dict[str, Any] = {
        "agent_runtime": True,
        "agent_runtime_version": "nexus.agent_runtime.v3",
        "agent_release_id": release.get("id"),
        "agent_release_version": release.get("version"),
        "agent_deployment_id": deployment.get("id"),
        "agent_release_digest": release.get("manifest_sha256"),
        "round_count": len(state.traces),
        "rounds": [item.as_dict() for item in state.traces[:8]],
        "executed_tools": list(state.executed_calls[:20]),
        "selected_playbooks": [
            str(item.get("resource_key") or item.get("name"))
            for item in (playbooks or [])[:30]
        ],
        "exposed_tools": [str(item.get("name")) for item in (tools or [])[:50]],
        "runtime_policy": {
            "max_tool_rounds": (policy or {}).get("max_tool_rounds"),
            "allow_high_risk_writes": bool(
                (policy or {}).get("allow_high_risk_writes")
            ),
        },
        "elapsed_ms": state.elapsed_ms,
    }
    if decision is not None:
        summary["ai_decision"] = decision.safe_public_summary()
    if provider_summary:
        summary["provider"] = provider_summary
    if error_code:
        summary["error_code"] = error_code[:160]
    return summary


def _round_request_id(base_request_id: str, round_index: int) -> str:
    return f"{base_request_id[:130]}:round:{round_index}"[:160]


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _optional_text(value: Any) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def _string_set(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    )


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
