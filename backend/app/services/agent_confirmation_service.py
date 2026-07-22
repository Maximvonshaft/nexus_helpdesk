from __future__ import annotations

import base64
import hashlib
import json
import secrets
import unicodedata
from datetime import timedelta
from typing import Any, Literal

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from ..models_agent_runtime import AgentToolConfirmation
from ..settings import get_settings
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatConversation, WebchatMessage

ConfirmationDecision = Literal["confirmed", "denied", "ambiguous"]

_DEFAULT_TTL_SECONDS = 10 * 60
_MAX_TTL_SECONDS = 30 * 60
_RECENT_RESULT_SECONDS = 15 * 60
_SENSITIVE_KEYS = {
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "raw_payload",
    "provider_payload",
    "encrypted_arguments",
}

_POSITIVE_RESPONSES = {
    "yes",
    "yes please",
    "please do",
    "go ahead",
    "do it",
    "confirm",
    "confirmed",
    "ok",
    "okay",
    "sure",
    "ja",
    "bitte",
    "oui",
    "d accord",
    "si",
    "sì",
    "sim",
    "da",
    "moze",
    "može",
    "可以",
    "可以的",
    "好的",
    "好",
    "同意",
    "确认",
    "是",
    "要",
    "请创建",
    "创建吧",
}
_NEGATIVE_RESPONSES = {
    "no",
    "no thanks",
    "do not",
    "dont",
    "don't",
    "cancel",
    "never mind",
    "nein",
    "non",
    "nope",
    "nao",
    "não",
    "ne",
    "nemoj",
    "不",
    "不要",
    "不用",
    "取消",
    "不需要",
    "先不用",
}


def _cipher() -> Fernet:
    settings = get_settings()
    root_secret = settings.jwt_secret_key
    if not root_secret:
        if settings.app_env == "production":
            raise RuntimeError("application secret is required for Agent confirmations")
        root_secret = "development-only-agent-confirmation-root"
    derived = hashlib.sha256(
        f"nexus.agent-confirmation.v1\x00{root_secret}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def canonical_tool_arguments(arguments: dict[str, Any] | None) -> str:
    return json.dumps(
        arguments or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def tool_arguments_sha256(arguments: dict[str, Any] | None) -> str:
    return hashlib.sha256(canonical_tool_arguments(arguments).encode("utf-8")).hexdigest()


def _seal_arguments(arguments: dict[str, Any]) -> str:
    return _cipher().encrypt(canonical_tool_arguments(arguments).encode("utf-8")).decode("ascii")


def open_confirmation_arguments(row: AgentToolConfirmation) -> dict[str, Any]:
    try:
        value = json.loads(_cipher().decrypt(row.encrypted_arguments.encode("ascii")))
    except (InvalidToken, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("agent_confirmation_arguments_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("agent_confirmation_arguments_invalid")
    if tool_arguments_sha256(value) != row.arguments_sha256:
        raise RuntimeError("agent_confirmation_arguments_digest_mismatch")
    return value


def _normalize_response(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    output: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category.startswith("P") or category.startswith("S"):
            output.append(" ")
        else:
            output.append(character)
    return " ".join("".join(output).split())[:120]


def classify_customer_confirmation(value: str) -> ConfirmationDecision:
    normalized = _normalize_response(value)
    if normalized in _POSITIVE_RESPONSES:
        return "confirmed"
    if normalized in _NEGATIVE_RESPONSES:
        return "denied"
    return "ambiguous"


def _question_for_tool(tool_name: str) -> str:
    if tool_name == "ticket.create":
        return (
            "Would you like me to create a support ticket so the team can follow up "
            "after this conversation?"
        )
    if tool_name == "speedaf.voice.callback":
        return "Would you like me to request a callback?"
    return "Would you like me to proceed with this action?"


def _safe_summary(tool_name: str, arguments: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "argument_keys": sorted(str(key)[:80] for key in arguments)[:20],
        "arguments_sha256_prefix": digest[:16],
    }


def _bounded_safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return " ".join(value.split())[:1000]
    if isinstance(value, (list, tuple, set)):
        return [
            _bounded_safe_value(item, depth=depth + 1)
            for item in list(value)[:20]
        ]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded_safe_value(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
            if str(key).strip().lower() not in _SENSITIVE_KEYS
        }
    return str(value)[:240]


def _active_query(db: Session, *, conversation_id: int):
    query = db.query(AgentToolConfirmation).filter(
        AgentToolConfirmation.conversation_id == conversation_id,
        AgentToolConfirmation.status.in_(["pending", "confirmed"]),
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    return query


def expire_confirmation_if_needed(row: AgentToolConfirmation) -> bool:
    expires_at = ensure_utc(row.expires_at)
    now = ensure_utc(utc_now())
    if expires_at is None or now is None or expires_at > now:
        return False
    if row.status in {"pending", "confirmed"}:
        row.status = "expired"
        row.resolved_at = row.resolved_at or now
        row.updated_at = now
        return True
    return False


def create_or_reuse_confirmation(
    db: Session,
    *,
    conversation: WebchatConversation,
    tool_name: str,
    arguments: dict[str, Any],
    question_text: str | None = None,
    requested_message_id: int | None = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> AgentToolConfirmation:
    normalized_tool = " ".join(str(tool_name or "").strip().split())[:160]
    if not normalized_tool:
        raise RuntimeError("agent_confirmation_tool_required")
    bounded_ttl = max(60, min(int(ttl_seconds or _DEFAULT_TTL_SECONDS), _MAX_TTL_SECONDS))
    digest = tool_arguments_sha256(arguments)
    now = utc_now()
    active = _active_query(db, conversation_id=conversation.id).first()
    if active is not None:
        expire_confirmation_if_needed(active)
        if (
            active.status == "pending"
            and active.tool_name == normalized_tool
            and active.arguments_sha256 == digest
        ):
            active.question_text = (question_text or active.question_text or _question_for_tool(normalized_tool))[:1000]
            active.expires_at = now + timedelta(seconds=bounded_ttl)
            active.requested_message_id = requested_message_id or active.requested_message_id
            active.updated_at = now
            db.flush()
            return active
        if active.status in {"pending", "confirmed"}:
            active.status = "cancelled"
            active.resolved_at = now
            active.updated_at = now
            db.flush()

    row = AgentToolConfirmation(
        public_id=f"ac_{secrets.token_urlsafe(18)}",
        tenant_key=conversation.tenant_key,
        conversation_id=conversation.id,
        tool_name=normalized_tool,
        arguments_sha256=digest,
        encrypted_arguments=_seal_arguments(arguments),
        safe_summary_json=_safe_summary(normalized_tool, arguments, digest),
        question_text=(question_text or _question_for_tool(normalized_tool))[:1000],
        status="pending",
        requested_message_id=requested_message_id,
        requested_at=now,
        expires_at=now + timedelta(seconds=bounded_ttl),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def resolve_confirmation_from_customer_message(
    db: Session,
    *,
    conversation: WebchatConversation,
    message: WebchatMessage,
) -> dict[str, Any] | None:
    row = _active_query(db, conversation_id=conversation.id).first()
    if row is None:
        return None
    if expire_confirmation_if_needed(row):
        db.flush()
        return confirmation_projection(row, decision="expired")
    if row.status != "pending":
        return confirmation_projection(row)
    decision = classify_customer_confirmation(message.body_text or message.body or "")
    if decision == "ambiguous":
        return confirmation_projection(row, decision=decision)
    now = utc_now()
    row.status = decision
    row.response_message_id = message.id
    row.resolved_at = now
    row.updated_at = now
    db.flush()
    return confirmation_projection(row, decision=decision)


def _recent_confirmation(
    db: Session,
    *,
    conversation_id: int,
) -> AgentToolConfirmation | None:
    cutoff = utc_now() - timedelta(seconds=_RECENT_RESULT_SECONDS)
    return (
        db.query(AgentToolConfirmation)
        .filter(
            AgentToolConfirmation.conversation_id == conversation_id,
            AgentToolConfirmation.status.in_(["consumed", "denied", "expired"]),
            AgentToolConfirmation.updated_at >= cutoff,
        )
        .order_by(AgentToolConfirmation.updated_at.desc(), AgentToolConfirmation.id.desc())
        .first()
    )


def active_confirmation_context(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> dict[str, Any] | None:
    row = _active_query(db, conversation_id=conversation.id).first()
    if row is not None:
        if expire_confirmation_if_needed(row):
            db.flush()
        else:
            return confirmation_projection(row)
    recent = _recent_confirmation(db, conversation_id=conversation.id)
    return confirmation_projection(recent) if recent is not None else None


def confirmation_projection(
    row: AgentToolConfirmation,
    *,
    decision: str | None = None,
) -> dict[str, Any]:
    return {
        "confirmation_id": row.public_id,
        "tool_name": row.tool_name,
        "status": row.status,
        "decision": decision,
        "question": row.question_text if row.status == "pending" else None,
        "safe_summary": dict(row.safe_summary_json or {}),
        "arguments_sha256": row.arguments_sha256,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "customer_confirmation_granted": row.status == "confirmed",
    }


def validate_confirmation_grant(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    confirmation_id: str | None,
    tool_name: str,
    arguments: dict[str, Any],
) -> AgentToolConfirmation | None:
    if conversation is None or not confirmation_id:
        return None
    query = db.query(AgentToolConfirmation).filter(
        AgentToolConfirmation.public_id == confirmation_id,
        AgentToolConfirmation.conversation_id == conversation.id,
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    row = query.first()
    if row is None or row.status != "confirmed":
        return None
    if expire_confirmation_if_needed(row):
        db.flush()
        return None
    if row.tenant_key != conversation.tenant_key:
        return None
    if row.tool_name != tool_name:
        return None
    if row.arguments_sha256 != tool_arguments_sha256(arguments):
        return None
    return row


def record_confirmation_execution_result(
    db: Session,
    *,
    confirmation_id: str,
    execution: dict[str, Any],
) -> AgentToolConfirmation | None:
    query = db.query(AgentToolConfirmation).filter(
        AgentToolConfirmation.public_id == confirmation_id
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    row = query.first()
    if row is None:
        return None
    summary = dict(row.safe_summary_json or {})
    summary["execution"] = _bounded_safe_value(execution)
    row.safe_summary_json = summary
    row.updated_at = utc_now()
    db.flush()
    return row


def consume_confirmation(
    db: Session,
    *,
    row: AgentToolConfirmation,
    tool_call_log_id: int | None,
) -> None:
    if row.status != "confirmed":
        raise RuntimeError("agent_confirmation_not_confirmed")
    now = utc_now()
    row.status = "consumed"
    row.consumed_tool_call_log_id = tool_call_log_id
    row.consumed_at = now
    row.updated_at = now
    db.flush()
