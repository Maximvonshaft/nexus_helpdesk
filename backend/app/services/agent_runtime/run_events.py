from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models_agent_control import AgentRun, AgentRunEvent
from ...utils.time import utc_now
from ..agent_release_service import ResolvedAgentRelease
from .observability import record_agent_event, record_agent_run

EVENT_TYPES = frozenset(
    {
        "run_started",
        "release_resolved",
        "context_compiled",
        "provider_started",
        "provider_completed",
        "provider_failed",
        "tool_requested",
        "tool_authorized",
        "tool_started",
        "tool_completed",
        "tool_failed",
        "clarification_requested",
        "handoff_committed",
        "reply_finalized",
        "fallback_used",
        "run_failed",
        "run_completed",
        "session_checkpoint_loaded",
        "session_checkpoint_saved",
    }
)

_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "run_started": frozenset({"runtime_version", "channel", "environment", "fork_kind"}),
    "release_resolved": frozenset(
        {"deployment_id", "release_id", "release_version", "release_digest", "canary"}
    ),
    "context_compiled": frozenset(
        {
            "budget_chars",
            "prompt_chars",
            "estimated_tokens",
            "compacted",
            "omitted_sections",
            "digest",
        }
    ),
    "provider_started": frozenset({"provider", "round_index", "effective_timeout_ms"}),
    "provider_completed": frozenset(
        {
            "provider",
            "round_index",
            "elapsed_ms",
            "model",
            "usage",
            "contract_repair_applied",
        }
    ),
    "provider_failed": frozenset(
        {"provider", "round_index", "elapsed_ms", "error_code", "retryable"}
    ),
    "tool_requested": frozenset({"tool_names", "round_index", "call_count"}),
    "tool_authorized": frozenset({"tool_name", "round_index", "status"}),
    "tool_started": frozenset({"tool_name", "round_index"}),
    "tool_completed": frozenset(
        {"tool_name", "round_index", "status", "elapsed_ms", "ok"}
    ),
    "tool_failed": frozenset(
        {"tool_name", "round_index", "status", "elapsed_ms", "error_code"}
    ),
    "clarification_requested": frozenset({"round_index", "intent"}),
    "handoff_committed": frozenset({"round_index", "reason_code"}),
    "reply_finalized": frozenset(
        {"round_index", "intent", "handoff_required", "reply_chars"}
    ),
    "fallback_used": frozenset({"error_code", "elapsed_ms"}),
    "run_failed": frozenset({"error_code", "elapsed_ms"}),
    "run_completed": frozenset(
        {"status", "final_action", "elapsed_ms", "round_count"}
    ),
    "session_checkpoint_loaded": frozenset(
        {"checkpoint_id", "checkpoint_version", "estimated_tokens", "release_id"}
    ),
    "session_checkpoint_saved": frozenset(
        {"checkpoint_id", "checkpoint_version", "estimated_tokens", "release_id"}
    ),
}

_FORBIDDEN_EXACT_KEYS = frozenset(
    {
        "prompt",
        "thought",
        "reasoning",
        "secret",
        "token",
        "password",
        "authorization",
        "cookie",
        "arguments",
        "raw_payload",
        "result_payload",
        "phone",
        "email",
        "address",
        "tracking_number",
        "waybill",
    }
)
_FORBIDDEN_SUFFIXES = tuple(f"_{key}" for key in _FORBIDDEN_EXACT_KEYS)


def start_agent_run(
    db: Session,
    *,
    request_id: str,
    session_id: str,
    tenant_key: str,
    channel: str,
    environment: str,
    runtime_version: str,
    parent_run_id: int | None = None,
    fork_kind: str | None = None,
    trace_id: str | None = None,
) -> AgentRun:
    request_id = _required_text(request_id, 160, "agent_run_request_id_required")
    session_id = _required_text(session_id, 160, "agent_run_session_id_required")
    tenant_key = _required_text(tenant_key, 80, "agent_run_tenant_required")
    existing = (
        db.query(AgentRun).filter(AgentRun.request_id == request_id).one_or_none()
    )
    if existing is not None:
        if existing.session_id != session_id or existing.tenant_key != tenant_key:
            raise RuntimeError("agent_run_idempotency_conflict")
        if existing.status != "running":
            raise RuntimeError("agent_run_already_terminal")
        return existing
    if fork_kind not in {None, "playground", "replay"}:
        raise RuntimeError("agent_run_fork_kind_invalid")
    if parent_run_id is not None:
        parent = db.get(AgentRun, parent_run_id)
        if parent is None or parent.tenant_key != tenant_key:
            raise RuntimeError("agent_run_parent_unavailable")
    row = AgentRun(
        request_id=request_id,
        session_id=session_id,
        tenant_key=tenant_key,
        trace_id=_trace_id(trace_id or request_id),
        parent_run_id=parent_run_id,
        fork_kind=fork_kind,
        status="running",
        elapsed_ms=0,
        started_at=utc_now(),
    )
    db.add(row)
    db.flush()
    append_agent_event(
        db,
        run=row,
        event_type="run_started",
        safe_payload={
            "runtime_version": runtime_version,
            "channel": str(channel or "")[:40],
            "environment": str(environment or "production")[:24],
            "fork_kind": fork_kind,
        },
    )
    return row


def bind_agent_run_release(
    db: Session,
    *,
    run: AgentRun,
    resolved: ResolvedAgentRelease,
) -> AgentRunEvent:
    if run.tenant_key != resolved.snapshot.get("tenant_key"):
        raise RuntimeError("agent_run_release_tenant_mismatch")
    run.deployment_id = resolved.deployment.id
    run.release_id = resolved.release.id
    run.release_digest = resolved.release.manifest_sha256
    db.flush()
    deployment = resolved.snapshot.get("deployment")
    return append_agent_event(
        db,
        run=run,
        event_type="release_resolved",
        safe_payload={
            "deployment_id": resolved.deployment.id,
            "release_id": resolved.release.id,
            "release_version": resolved.release.version,
            "release_digest": resolved.release.manifest_sha256,
            "canary": bool(
                deployment.get("canary") if isinstance(deployment, dict) else False
            ),
        },
    )


def append_agent_event(
    db: Session,
    *,
    run: AgentRun,
    event_type: str,
    safe_payload: dict[str, Any] | None = None,
    round_index: int | None = None,
    parent_event_id: int | None = None,
    status: str = "recorded",
    duration_ms: int = 0,
) -> AgentRunEvent:
    if event_type not in EVENT_TYPES:
        raise RuntimeError("agent_run_event_type_invalid")
    if run.id is None:
        db.flush()
    locked = (
        db.query(AgentRun)
        .filter(AgentRun.id == run.id)
        .with_for_update()
        .one()
    )
    max_sequence = (
        db.query(func.max(AgentRunEvent.sequence))
        .filter(AgentRunEvent.run_id == locked.id)
        .scalar()
    )
    if parent_event_id is not None:
        parent = db.get(AgentRunEvent, parent_event_id)
        if parent is None or parent.run_id != locked.id:
            raise RuntimeError("agent_run_event_parent_invalid")
    row = AgentRunEvent(
        run_id=locked.id,
        sequence=int(max_sequence or 0) + 1,
        event_type=event_type,
        round_index=(
            max(0, int(round_index)) if round_index is not None else None
        ),
        parent_event_id=parent_event_id,
        status=_safe_token(status, 40, "recorded"),
        duration_ms=max(0, min(int(duration_ms or 0), 3_600_000)),
        safe_payload_json=_event_payload(event_type, safe_payload or {}),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    record_agent_event(event_type, row.status)
    return row


def finish_agent_run(
    db: Session,
    *,
    run: AgentRun,
    status: str,
    final_action: str | None,
    elapsed_ms: int,
    error_code: str | None = None,
    round_count: int = 0,
) -> AgentRun:
    """Atomically persist checkpoint, terminal event and terminal lifecycle state."""

    if status not in {"succeeded", "fallback", "failed", "cancelled"}:
        raise RuntimeError("agent_run_terminal_status_invalid")
    if run.status != "running":
        if (
            run.status == status
            and run.final_action == _optional_token(final_action, 80)
            and run.error_code == _optional_token(error_code, 160)
        ):
            return run
        raise RuntimeError("agent_run_terminal_conflict")

    run.status = status
    run.final_action = _optional_token(final_action, 80)
    run.error_code = _optional_token(error_code, 160)
    run.elapsed_ms = max(0, min(int(elapsed_ms or 0), 3_600_000))
    run.completed_at = utc_now()

    try:
        if run.release_id is not None and status in {"succeeded", "fallback"}:
            _persist_terminal_checkpoint(
                db,
                run=run,
                status=status,
                round_count=round_count,
            )
        event_type = "run_failed" if status == "failed" else "run_completed"
        append_agent_event(
            db,
            run=run,
            event_type=event_type,
            safe_payload={
                "status": status,
                "final_action": run.final_action,
                "elapsed_ms": run.elapsed_ms,
                "error_code": run.error_code,
                "round_count": max(0, min(int(round_count or 0), 100)),
            },
            status=status,
            duration_ms=run.elapsed_ms,
        )
        db.flush()
        db.commit()
    except Exception as exc:
        db.rollback()
        raise RuntimeError("agent_run_terminal_commit_failed") from exc

    record_agent_run(
        status=status,
        final_action=run.final_action,
        elapsed_ms=run.elapsed_ms,
    )
    return run


def _persist_terminal_checkpoint(
    db: Session,
    *,
    run: AgentRun,
    status: str,
    round_count: int,
) -> None:
    from .session_checkpoints import (
        build_checkpoint_summary,
        load_session_checkpoint,
        save_session_checkpoint,
    )

    events = (
        db.query(AgentRunEvent)
        .filter(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.sequence.asc())
        .all()
    )
    latest_intent: str | None = None
    handoff_required = False
    tool_outcomes: list[dict[str, Any]] = []
    for event in events:
        payload = event.safe_payload_json if isinstance(event.safe_payload_json, dict) else {}
        if event.event_type in {"reply_finalized", "clarification_requested"}:
            candidate = str(payload.get("intent") or "").strip()
            if candidate:
                latest_intent = candidate[:120]
            handoff_required = handoff_required or payload.get("handoff_required") is True
        if event.event_type in {"tool_completed", "tool_failed"}:
            tool_outcomes.append(
                {
                    "tool_name": payload.get("tool_name"),
                    "status": payload.get("status"),
                    "ok": event.event_type == "tool_completed",
                    "error_code": payload.get("error_code"),
                }
            )
    prior = load_session_checkpoint(
        db,
        tenant_key=run.tenant_key,
        session_id=run.session_id,
        release_id=int(run.release_id),
    )
    summary = build_checkpoint_summary(
        intent=latest_intent,
        final_action=run.final_action,
        run_status=status,
        round_count=round_count,
        handoff_required=handoff_required,
        tool_calls=tool_outcomes,
        prior_checkpoint=prior,
    )
    checkpoint = save_session_checkpoint(db, run=run, summary=summary)
    append_agent_event(
        db,
        run=run,
        event_type="session_checkpoint_saved",
        safe_payload={
            "checkpoint_id": checkpoint.id,
            "checkpoint_version": checkpoint.version,
            "estimated_tokens": checkpoint.estimated_tokens,
            "release_id": checkpoint.release_id,
        },
    )


def agent_run_payload(row: AgentRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "request_id": row.request_id,
        "session_id": row.session_id,
        "tenant_key": row.tenant_key,
        "trace_id": row.trace_id,
        "deployment_id": row.deployment_id,
        "release_id": row.release_id,
        "release_digest": row.release_digest,
        "parent_run_id": row.parent_run_id,
        "fork_kind": row.fork_kind,
        "status": row.status,
        "final_action": row.final_action,
        "error_code": row.error_code,
        "elapsed_ms": row.elapsed_ms,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


def agent_event_payload(row: AgentRunEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "sequence": row.sequence,
        "event_type": row.event_type,
        "round_index": row.round_index,
        "parent_event_id": row.parent_event_id,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "safe_payload": row.safe_payload_json or {},
        "created_at": row.created_at,
    }


def _event_payload(event_type: str, value: dict[str, Any]) -> dict[str, Any]:
    allowed = _EVENT_FIELDS[event_type]
    output: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:80]:
        key = str(raw_key or "").strip()
        if key not in allowed:
            continue
        safe = _safe_value(raw_value)
        if safe is not None:
            output[key] = safe
    encoded = json.dumps(
        output,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(encoded) > 16000:
        raise RuntimeError("agent_run_event_payload_too_large")
    return output


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, (list, tuple)):
        return [
            item
            for raw in list(value)[:40]
            if (item := _safe_value(raw, depth=depth + 1)) is not None
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_item in list(value.items())[:40]:
            key = str(raw_key or "").strip()[:80]
            if not key or _forbidden_key(key):
                continue
            item = _safe_value(raw_item, depth=depth + 1)
            if item is not None:
                result[key] = item
        return result
    return None


def _forbidden_key(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in _FORBIDDEN_EXACT_KEYS or lowered.endswith(_FORBIDDEN_SUFFIXES)


def _required_text(value: Any, limit: int, error: str) -> str:
    cleaned = str(value or "").strip()[:limit]
    if not cleaned:
        raise RuntimeError(error)
    return cleaned


def _safe_token(value: Any, limit: int, default: str) -> str:
    cleaned = "_".join(str(value or default).strip().lower().split())[:limit]
    return cleaned or default


def _optional_token(value: Any, limit: int) -> str | None:
    cleaned = "_".join(str(value or "").strip().lower().split())[:limit]
    return cleaned or None


def _trace_id(seed: str) -> str:
    candidate = str(seed or "").strip().lower()
    if 16 <= len(candidate) <= 64 and all(
        character in "0123456789abcdef-" for character in candidate
    ):
        return candidate.replace("-", "")[:64]
    return hashlib.sha256(
        f"{candidate}:{uuid.uuid4().hex}".encode("utf-8")
    ).hexdigest()
