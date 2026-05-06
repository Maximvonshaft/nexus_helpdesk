from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..tool_models import ToolCallLog, ToolRegistry
from ..utils.time import utc_now
from .observability import record_tool_call_metric

LOGGER = logging.getLogger("nexusdesk")

SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|credential|cookie|session|prompt|system|developer)",
    re.IGNORECASE,
)
TEXT_KEY_RE = re.compile(r"(text|body|message|content|reply|description|summary|transcript)", re.IGNORECASE)
SAFE_STATUS_KEYS = {"ok", "status", "tool_status", "error", "error_code", "failure_reason", "reason", "method", "channel"}
MAX_SUMMARY_CHARS = 1200

READ_TOOLS = {
    "conversations_list",
    "conversation_get",
    "messages_read",
    "attachments_fetch",
    "events_poll",
    "events_wait",
    "openclaw_bridge.speedaf_lookup",
    "tracking_fact_lookup",
}
WRITE_TOOLS = {"messages_send"}
EXTERNAL_SEND_TOOLS = {"messages_send", "openclaw_bridge.messages_send"}
SYSTEM_TOOLS = {"openclaw_bridge.ai_reply"}
VALID_ENFORCEMENT_MODES = {"off", "audit_only", "enforce"}


class ToolPolicyBlocked(RuntimeError):
    def __init__(self, decision: "ToolPolicyDecision") -> None:
        self.decision = decision
        super().__init__(f"tool_policy_blocked:{decision.tool_name}:{decision.reason_code}")


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    mode: str
    tool_name: str
    tool_type: str
    risk_level: str
    reason_code: str
    required_capability: str | None = None
    audit_only: bool = True

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _enforcement_mode() -> str:
    mode = os.getenv("TOOL_GOVERNANCE_ENFORCEMENT_MODE", "audit_only").strip().lower() or "audit_only"
    if mode not in VALID_ENFORCEMENT_MODES:
        return "audit_only"
    return mode


def classify_tool_type(tool_name: str) -> str:
    normalized = (tool_name or "").strip()
    lowered = normalized.lower()
    if normalized in EXTERNAL_SEND_TOOLS or lowered.endswith(".send") or lowered.endswith("_send"):
        return "external_send"
    if normalized in WRITE_TOOLS or normalized.endswith(".messages_send"):
        return "write_action"
    if normalized in SYSTEM_TOOLS or normalized.endswith(".ai_reply"):
        return "system"
    if normalized in READ_TOOLS or normalized.startswith("openclaw_bridge.speedaf_lookup"):
        return "read_only"
    return "read_only"


def _risk_for_tool_type(tool_type: str) -> str:
    if tool_type == "external_send":
        return "critical"
    if tool_type == "write_action":
        return "high"
    if tool_type == "system":
        return "medium"
    return "low"


def _retry_policy_for_type(tool_type: str) -> str:
    if tool_type in {"external_send", "write_action"}:
        return "no_auto_retry_without_idempotency"
    return "read_retry_allowed"


def _required_capability(tool_name: str, tool_type: str) -> str | None:
    if tool_type == "external_send":
        return f"tool:{tool_name}:external_send"
    if tool_type == "write_action":
        return f"tool:{tool_name}:write"
    return None


def _has_capability(capabilities: Iterable[str] | None, required: str | None) -> bool:
    if not required:
        return True
    return required in set(capabilities or [])


def evaluate_tool_call_policy(
    *,
    tool_name: str,
    tool_type: str | None = None,
    actor_capabilities: Iterable[str] | None = None,
) -> ToolPolicyDecision:
    resolved_type = tool_type or classify_tool_type(tool_name)
    risk_level = _risk_for_tool_type(resolved_type)
    mode = _enforcement_mode()
    required = _required_capability(tool_name, resolved_type)
    audit_only = mode != "enforce"

    if mode == "off":
        return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, "governance_off", required, True)

    if resolved_type == "read_only" or resolved_type == "system":
        return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, "read_or_system_allowed", required, audit_only)

    is_external_send = resolved_type == "external_send"
    is_write = resolved_type == "write_action"
    require_write = _env_bool("TOOL_GOVERNANCE_REQUIRE_CAPABILITY_FOR_WRITE", True)
    require_external = _env_bool("TOOL_GOVERNANCE_REQUIRE_CAPABILITY_FOR_EXTERNAL_SEND", True)
    block_write = _env_bool("TOOL_GOVERNANCE_BLOCK_WRITE_TOOLS", True)

    needs_capability = (is_write and require_write) or (is_external_send and require_external)
    capability_ok = _has_capability(actor_capabilities, required)
    should_block = block_write or (needs_capability and not capability_ok)
    reason = "write_or_external_send_allowed_with_capability" if capability_ok else "write_or_external_send_requires_capability"

    if should_block and mode == "audit_only":
        return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, f"would_block:{reason}", required, True)
    if should_block and mode == "enforce":
        return ToolPolicyDecision(False, mode, tool_name, resolved_type, risk_level, reason, required, False)
    return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, reason, required, audit_only)


def enforce_tool_policy(
    *,
    tool_name: str,
    tool_type: str | None = None,
    actor_capabilities: Iterable[str] | None = None,
) -> ToolPolicyDecision:
    decision = evaluate_tool_call_policy(tool_name=tool_name, tool_type=tool_type, actor_capabilities=actor_capabilities)
    if not decision.allowed:
        raise ToolPolicyBlocked(decision)
    return decision


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8", errors="ignore")).hexdigest()


def _summarize_scalar(key: str | None, value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return f"{type(value).__name__}"
    cleaned = value.strip()
    key = key or ""
    digest = hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:16]
    if SENSITIVE_KEY_RE.search(key):
        return {"redacted": True, "type": "string", "length": len(cleaned), "sha256_prefix": digest}
    if TEXT_KEY_RE.search(key):
        return {"redacted": True, "type": "text", "length": len(cleaned), "sha256_prefix": digest}
    if key in SAFE_STATUS_KEYS and len(cleaned) <= 80:
        return cleaned
    if len(cleaned) <= 24 and re.fullmatch(r"[A-Za-z0-9_.:@/-]+", cleaned):
        return {"value_preview": cleaned[:4] + "…" if len(cleaned) > 4 else cleaned, "length": len(cleaned), "sha256_prefix": digest}
    return {"redacted": True, "type": "string", "length": len(cleaned), "sha256_prefix": digest}


def _summarize_payload(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if depth > 3:
        return {"truncated": True, "type": type(value).__name__, "sha256_prefix": _hash_value(value)[:16]}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {"_type": "object", "_keys": sorted(str(k) for k in value.keys())[:30]}
        for item_key, item_value in list(value.items())[:30]:
            result[str(item_key)] = _summarize_payload(item_value, key=str(item_key), depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)[:10]
        return {"_type": "array", "_count_visible": len(items), "items": [_summarize_payload(item, key=key, depth=depth + 1) for item in items]}
    return _summarize_scalar(key, value)


def summarize_input_safe(payload: Any) -> str:
    summary = _stable_json(_summarize_payload(payload))
    return summary[:MAX_SUMMARY_CHARS]


def summarize_output_safe(payload: Any) -> str:
    summary = _stable_json(_summarize_payload(payload))
    return summary[:MAX_SUMMARY_CHARS]


def get_or_create_tool_registry_entry(
    db: Session,
    *,
    tool_name: str,
    provider: str = "openclaw",
    tool_type: str | None = None,
    default_timeout_ms: int | None = None,
    max_timeout_ms: int | None = None,
    description: str | None = None,
) -> ToolRegistry | None:
    tool_name = (tool_name or "unknown_tool").strip()[:160]
    resolved_type = tool_type or classify_tool_type(tool_name)
    row = db.query(ToolRegistry).filter(ToolRegistry.tool_name == tool_name).first()
    if row is None:
        row = ToolRegistry(
            tool_name=tool_name,
            provider=(provider or "openclaw")[:80],
            tool_type=resolved_type,
            default_timeout_ms=default_timeout_ms,
            max_timeout_ms=max_timeout_ms,
            retry_policy=_retry_policy_for_type(resolved_type),
            risk_level=_risk_for_tool_type(resolved_type),
            enabled=True,
            audit_enabled=True,
            description=description,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.flush()
    else:
        changed = False
        if row.tool_type != resolved_type:
            row.tool_type = resolved_type
            row.risk_level = _risk_for_tool_type(resolved_type)
            row.retry_policy = _retry_policy_for_type(resolved_type)
            changed = True
        if default_timeout_ms is not None and row.default_timeout_ms != default_timeout_ms:
            row.default_timeout_ms = default_timeout_ms
            changed = True
        if max_timeout_ms is not None and row.max_timeout_ms != max_timeout_ms:
            row.max_timeout_ms = max_timeout_ms
            changed = True
        if changed:
            row.updated_at = utc_now()
            db.flush()
    return row


def _error_code_for(status: str, error_code: str | None, error_message: str | None) -> str | None:
    if error_code:
        return error_code[:120]
    if status == "timeout":
        return "timeout"
    if status in {"failed", "blocked"}:
        return "tool_call_failed"
    if error_message:
        return "tool_call_error"
    return None


def record_tool_call(
    *,
    tool_name: str,
    provider: str = "openclaw",
    tool_type: str | None = None,
    input_payload: Any = None,
    output_payload: Any = None,
    status: str = "success",
    error_code: str | None = None,
    error_message: str | None = None,
    elapsed_ms: int | None = None,
    timeout_ms: int | None = None,
    conversation_id: str | None = None,
    webchat_conversation_id: int | None = None,
    ticket_id: int | None = None,
    ai_turn_id: int | None = None,
    background_job_id: int | None = None,
    actor_type: str | None = None,
    actor_id: int | None = None,
    request_id: str | None = None,
    policy_decision: ToolPolicyDecision | None = None,
    db: Session | None = None,
) -> None:
    """Record a safe audit-only tool call.

    This function is intentionally best-effort. It must never break customer
    replies, OpenClaw sync, or outbound operations if the audit schema has not
    been migrated yet or the audit insert fails.
    """
    resolved_type = tool_type or classify_tool_type(tool_name)
    if policy_decision and policy_decision.reason_code.startswith("would_block") and status == "success":
        status = "would_block"
    safe_status = (status or "success")[:40]
    owns_session = db is None
    session = db or SessionLocal()
    try:
        get_or_create_tool_registry_entry(
            session,
            tool_name=tool_name,
            provider=provider,
            tool_type=resolved_type,
            default_timeout_ms=timeout_ms,
            max_timeout_ms=timeout_ms,
            description=json.dumps(policy_decision.as_payload(), ensure_ascii=False) if policy_decision else None,
        )
        row = ToolCallLog(
            tool_name=(tool_name or "unknown_tool")[:160],
            provider=(provider or "openclaw")[:80],
            tool_type=resolved_type,
            conversation_id=conversation_id[:160] if conversation_id else None,
            webchat_conversation_id=webchat_conversation_id,
            ticket_id=ticket_id,
            ai_turn_id=ai_turn_id,
            background_job_id=background_job_id,
            actor_type=actor_type[:80] if actor_type else None,
            actor_id=actor_id,
            request_id=request_id[:160] if request_id else None,
            input_hash=_hash_value(input_payload) if input_payload is not None else None,
            input_summary=summarize_input_safe({"payload": input_payload, "policy": policy_decision.as_payload() if policy_decision else None}) if input_payload is not None or policy_decision else None,
            output_hash=_hash_value(output_payload) if output_payload is not None else None,
            output_summary=summarize_output_safe(output_payload) if output_payload is not None else None,
            status=safe_status,
            error_code=_error_code_for(safe_status, error_code, error_message),
            error_message=(error_message or "")[:500] or None,
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms,
            redaction_applied=True,
            created_at=utc_now(),
        )
        session.add(row)
        if owns_session:
            session.commit()
        else:
            session.flush()
    except Exception as exc:  # pragma: no cover - audit-only must not break runtime
        try:
            session.rollback()
        except Exception:
            pass
        LOGGER.warning(
            "tool_governance_audit_failed",
            extra={"event_payload": {"tool_name": tool_name, "provider": provider, "status": safe_status, "error": str(exc)[:300]}},
        )
    finally:
        try:
            record_tool_call_metric(tool_name=tool_name, tool_type=resolved_type, status=safe_status, elapsed_ms=elapsed_ms)
        except Exception:
            pass
        if owns_session:
            session.close()
