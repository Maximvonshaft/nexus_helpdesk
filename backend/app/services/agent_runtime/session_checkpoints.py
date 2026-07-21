from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models_agent_control import AgentRun
from ...models_agent_runtime import AgentSessionCheckpoint
from ...utils.time import utc_now

CHECKPOINT_SCHEMA = "nexus.agent_session_checkpoint.v1"
_DEFAULT_TTL_SECONDS = 86400
_MIN_TTL_SECONDS = 300
_MAX_TTL_SECONDS = 604800
_ALLOWED_SUMMARY_KEYS = frozenset(
    {
        "schema_version",
        "last_intent",
        "last_final_action",
        "run_status",
        "round_count",
        "handoff_required",
        "tool_outcomes",
        "prior_checkpoint_version",
    }
)
_FORBIDDEN_MARKERS = (
    "message",
    "reply",
    "prompt",
    "thought",
    "reasoning",
    "argument",
    "result",
    "secret",
    "token",
    "password",
    "authorization",
    "phone",
    "email",
    "address",
    "tracking",
    "waybill",
)


def load_session_checkpoint(
    db: Session,
    *,
    tenant_key: str,
    session_id: str,
    release_id: int,
) -> AgentSessionCheckpoint | None:
    """Read the latest non-expired checkpoint without mutating context state."""

    now = utc_now()
    return (
        db.query(AgentSessionCheckpoint)
        .filter(
            AgentSessionCheckpoint.tenant_key == tenant_key,
            AgentSessionCheckpoint.session_id == session_id,
            AgentSessionCheckpoint.release_id == release_id,
            AgentSessionCheckpoint.is_active.is_(True),
            AgentSessionCheckpoint.expires_at > now,
        )
        .order_by(
            AgentSessionCheckpoint.version.desc(),
            AgentSessionCheckpoint.id.desc(),
        )
        .first()
    )


def checkpoint_prompt_projection(row: AgentSessionCheckpoint | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "checkpoint_id": row.id,
        "checkpoint_version": row.version,
        "release_id": row.release_id,
        "summary_sha256": row.summary_sha256,
        "estimated_tokens": row.estimated_tokens,
        "summary": _safe_summary(row.summary_json),
        "expires_at": row.expires_at.isoformat(),
    }


def save_session_checkpoint(
    db: Session,
    *,
    run: AgentRun,
    summary: dict[str, Any],
    ttl_seconds: int | None = None,
) -> AgentSessionCheckpoint:
    if run.id is None or run.release_id is None:
        raise RuntimeError("agent_checkpoint_run_release_required")
    ttl = _bounded_ttl(ttl_seconds)
    now = utc_now()
    safe_summary = _safe_summary(summary)
    safe_summary["schema_version"] = CHECKPOINT_SCHEMA
    payload = json.dumps(
        safe_summary,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(payload) > 12000:
        raise RuntimeError("agent_checkpoint_payload_too_large")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    estimated_tokens = max(1, (len(payload) + 3) // 4)

    active = (
        db.query(AgentSessionCheckpoint)
        .filter(
            AgentSessionCheckpoint.tenant_key == run.tenant_key,
            AgentSessionCheckpoint.session_id == run.session_id,
            AgentSessionCheckpoint.is_active.is_(True),
        )
        .with_for_update()
        .all()
    )
    for row in active:
        row.is_active = False
        row.deactivated_at = now
    latest = (
        db.query(func.max(AgentSessionCheckpoint.version))
        .filter(
            AgentSessionCheckpoint.tenant_key == run.tenant_key,
            AgentSessionCheckpoint.session_id == run.session_id,
        )
        .scalar()
    )
    row = AgentSessionCheckpoint(
        tenant_key=run.tenant_key,
        session_id=run.session_id,
        release_id=run.release_id,
        source_run_id=run.id,
        version=int(latest or 0) + 1,
        summary_sha256=digest,
        summary_json=safe_summary,
        estimated_tokens=estimated_tokens,
        is_active=True,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    db.add(row)
    db.flush()
    return row


def build_checkpoint_summary(
    *,
    intent: str | None,
    final_action: str | None,
    run_status: str,
    round_count: int,
    handoff_required: bool,
    tool_calls: list[dict[str, Any]],
    prior_checkpoint: AgentSessionCheckpoint | None = None,
) -> dict[str, Any]:
    outcomes = []
    for item in tool_calls[:20]:
        if not isinstance(item, dict):
            continue
        outcomes.append(
            {
                "tool_name": _token(item.get("tool_name"), 160),
                "status": _token(item.get("status"), 40),
                "ok": item.get("ok") is True,
                "error_code": _optional_token(item.get("error_code"), 160),
            }
        )
    return _safe_summary(
        {
            "schema_version": CHECKPOINT_SCHEMA,
            "last_intent": _optional_token(intent, 120),
            "last_final_action": _optional_token(final_action, 80),
            "run_status": _token(run_status, 24),
            "round_count": max(0, min(int(round_count or 0), 100)),
            "handoff_required": bool(handoff_required),
            "tool_outcomes": outcomes,
            "prior_checkpoint_version": (
                prior_checkpoint.version if prior_checkpoint is not None else None
            ),
        }
    )


def _safe_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"schema_version": CHECKPOINT_SCHEMA}
    output: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:40]:
        key = str(raw_key or "").strip()
        if key not in _ALLOWED_SUMMARY_KEYS or _forbidden(key):
            continue
        if key == "tool_outcomes":
            output[key] = _safe_tool_outcomes(raw_value)
        elif raw_value is None or isinstance(raw_value, (bool, int)):
            output[key] = raw_value
        elif isinstance(raw_value, str):
            output[key] = raw_value[:240]
    output["schema_version"] = CHECKPOINT_SCHEMA
    return output


def _safe_tool_outcomes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for raw in value[:20]:
        if not isinstance(raw, dict):
            continue
        output.append(
            {
                "tool_name": _token(raw.get("tool_name"), 160),
                "status": _token(raw.get("status"), 40),
                "ok": raw.get("ok") is True,
                "error_code": _optional_token(raw.get("error_code"), 160),
            }
        )
    return output


def _forbidden(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _FORBIDDEN_MARKERS)


def _bounded_ttl(value: int | None) -> int:
    try:
        parsed = int(value if value is not None else _DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        parsed = _DEFAULT_TTL_SECONDS
    return max(_MIN_TTL_SECONDS, min(_MAX_TTL_SECONDS, parsed))


def _token(value: Any, limit: int) -> str:
    return "_".join(str(value or "unknown").strip().lower().split())[:limit] or "unknown"


def _optional_token(value: Any, limit: int) -> str | None:
    cleaned = "_".join(str(value or "").strip().lower().split())[:limit]
    return cleaned or None
