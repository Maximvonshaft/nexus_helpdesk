from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .webcall_ai.demo_lab import get_demo_lab_status
from .webchat_runtime_config import get_webchat_runtime_settings


HUMAN_WEBCALL_RINGING_STATUSES = {"created", "ringing"}
HUMAN_WEBCALL_ACTIVE_STATUSES = {"accepted", "active"}


def _provider_capabilities(*, webchat_runtime_reply: bool, handoff_decision: bool = True) -> dict[str, bool]:
    return {
        "webchat_runtime_reply": webchat_runtime_reply,
        "handoff_decision": handoff_decision,
        "streaming": False,
        "tool_execution": False,
        "ticket_action": False,
        "direct_customer_outbound_send": False,
    }


def _provider_entry(
    *,
    name: str,
    selected: bool,
    feature_enabled: bool,
    configured: bool,
    runtime: str,
    capabilities: dict[str, bool],
    controls: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "selected": selected,
        "feature_enabled": feature_enabled,
        "configured": configured,
        "runtime": runtime,
        "safety_level": "reply_only",
        "capabilities": capabilities,
        "controls": controls or {},
        "diagnostics": diagnostics or {},
        "boundary": {
            "browser_cookie_scraping": False,
            "chatgpt_session_scraping": False,
            "shell_execution": False,
            "file_write": False,
            "model_native_tool_execution": False,
            "direct_ticket_action": False,
            "direct_customer_outbound_send": False,
        },
    }


def _private_ai_runtime_status_from_env() -> dict[str, Any]:
    primary_provider = os.environ.get("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "").strip() or "private_ai_runtime"
    enabled = os.environ.get("PRIVATE_AI_RUNTIME_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    base_url = os.environ.get("PRIVATE_AI_RUNTIME_BASE_URL", "").strip()
    rag_base_url = (os.environ.get("PRIVATE_AI_RUNTIME_RAG_BASE_URL") or base_url).strip()
    token_file = os.environ.get("PRIVATE_AI_RUNTIME_TOKEN_FILE", "").strip()
    inline_token = os.environ.get("PRIVATE_AI_RUNTIME_TOKEN", "").strip()
    direct_path = os.environ.get("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/api/chat").strip() or "/api/chat"
    rag_path = os.environ.get("PRIVATE_AI_RUNTIME_RAG_PATH", "/api/chat").strip() or "/api/chat"
    chat_mode = os.environ.get("PRIVATE_AI_RUNTIME_CHAT_MODE", "direct").strip().lower() or "direct"
    request_shape = os.environ.get("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat").strip().lower() or "ollama_chat"
    direct_model = os.environ.get("PRIVATE_AI_RUNTIME_DIRECT_MODEL", "qwen2.5:3b").strip() or "qwen2.5:3b"
    rag_model = os.environ.get("PRIVATE_AI_RUNTIME_RAG_MODEL", "qwen3:4b").strip() or "qwen3:4b"
    allow_shared_rag_model = os.environ.get("PRIVATE_AI_RUNTIME_ALLOW_SHARED_RAG_MODEL", "false").strip().lower() in {"1", "true", "yes", "on"}
    app_env = (os.environ.get("APP_ENV") or "development").strip().lower()
    inline_token_allowed = app_env in {"development", "test", "local"}
    configured = bool(enabled and base_url and (token_file or (inline_token and inline_token_allowed)))
    rag_runtime_isolated = bool(rag_base_url and not _same_runtime_origin(base_url, rag_base_url))
    rag_isolation_error = (
        app_env == "production"
        and chat_mode in {"rag", "auto"}
        and rag_model != direct_model
        and not rag_runtime_isolated
        and not allow_shared_rag_model
    )
    return {
        "primary_provider": primary_provider,
        "enabled": enabled,
        "base_url_configured": bool(base_url),
        "rag_base_url_configured": bool(rag_base_url),
        "rag_runtime_isolated": rag_runtime_isolated,
        "allow_shared_rag_model": allow_shared_rag_model,
        "token_file_configured": bool(token_file),
        "inline_token_configured": bool(inline_token),
        "configured": configured,
        "chat_mode": chat_mode,
        "direct_path": _safe_path(direct_path),
        "rag_path": _safe_path(rag_path),
        "request_shape": request_shape,
        "direct_model": direct_model,
        "rag_model": rag_model,
        "timeout_seconds": os.environ.get("PRIVATE_AI_RUNTIME_TIMEOUT_SECONDS", "8").strip() or "8",
        "shape_mismatch": _known_endpoint_shape_mismatch(direct_path, request_shape, endpoint_kind="direct")
        or (_known_endpoint_shape_mismatch(rag_path, request_shape, endpoint_kind="rag") if chat_mode in {"rag", "auto"} else None),
        "rag_isolation_error": rag_isolation_error,
    }


def _human_webcall_count(db: Session | None, statuses: set[str], *, stale: bool = False) -> int:
    if db is None:
        return 0
    query = db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.status.in_(sorted(statuses)),
        WebchatVoiceSession.ended_at.is_(None),
    )
    if stale:
        query = query.filter(
            WebchatVoiceSession.expires_at.isnot(None),
            WebchatVoiceSession.expires_at < utc_now(),
        )
    return int(query.count())


def get_human_webcall_runtime_status(db: Session | None = None) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        config = load_webchat_voice_runtime_config()
        webchat_voice_enabled = config.enabled
        provider = config.provider
        recording_enabled = config.recording_enabled
        transcription_enabled = config.transcription_enabled
        if recording_enabled:
            warnings.append("human_webcall recording is enabled")
        if transcription_enabled:
            warnings.append("human_webcall transcription is enabled")
    except RuntimeError as exc:
        webchat_voice_enabled = False
        provider = "unknown"
        recording_enabled = False
        transcription_enabled = False
        warnings.append(str(exc))

    try:
        active_session_count = _human_webcall_count(db, {"active"})
        ringing_session_count = _human_webcall_count(db, HUMAN_WEBCALL_RINGING_STATUSES)
        stale_active_session_count = _human_webcall_count(db, HUMAN_WEBCALL_ACTIVE_STATUSES, stale=True)
        stale_ringing_session_count = _human_webcall_count(db, HUMAN_WEBCALL_RINGING_STATUSES, stale=True)
    except (AttributeError, TypeError, SQLAlchemyError) as exc:
        active_session_count = 0
        ringing_session_count = 0
        stale_active_session_count = 0
        stale_ringing_session_count = 0
        warnings.append(f"human_webcall status unavailable: {type(exc).__name__}")

    if not webchat_voice_enabled:
        readiness_verdict = "disabled"
    elif warnings:
        readiness_verdict = "warning"
    else:
        readiness_verdict = "ready"

    return {
        "webchat_voice_enabled": webchat_voice_enabled,
        "provider": provider,
        "recording_enabled": recording_enabled,
        "transcription_enabled": transcription_enabled,
        "active_session_count": active_session_count,
        "ringing_session_count": ringing_session_count,
        "stale_active_session_count": stale_active_session_count,
        "stale_ringing_session_count": stale_ringing_session_count,
        "readiness_verdict": readiness_verdict,
        "warnings": warnings,
    }


def get_provider_runtime_status(db: Session | None = None) -> dict[str, Any]:
    """Return a safe, secret-free Provider Runtime status snapshot.

    This is intentionally a configuration/readiness surface, not a live upstream
    probe. It must never echo token values, upstream payloads, customer messages,
    or raw provider responses.
    """

    try:
        settings = get_webchat_runtime_settings()
    except RuntimeError as exc:
        return {
            "ok": False,
            "status": "misconfigured",
            "config_error": str(exc),
            "providers": [],
            "boundary": {
                "secret_values_exposed": False,
                "external_network_call": False,
                "customer_message_sent": False,
            },
        }

    private_ai_runtime = _private_ai_runtime_status_from_env()

    providers = [
        _provider_entry(
            name="private_ai_runtime",
            selected=True,
            feature_enabled=private_ai_runtime["enabled"],
            configured=private_ai_runtime["configured"],
            runtime="server_side_ai_runtime",
            capabilities=_provider_capabilities(webchat_runtime_reply=private_ai_runtime["configured"]),
            diagnostics={
                "primary_provider": private_ai_runtime["primary_provider"],
                "base_url_configured": private_ai_runtime["base_url_configured"],
                "rag_base_url_configured": private_ai_runtime["rag_base_url_configured"],
                "rag_runtime_isolated": private_ai_runtime["rag_runtime_isolated"],
                "allow_shared_rag_model": private_ai_runtime["allow_shared_rag_model"],
                "token_file_configured": private_ai_runtime["token_file_configured"],
                "inline_token_configured": private_ai_runtime["inline_token_configured"],
                "chat_mode": private_ai_runtime["chat_mode"],
                "direct_path": private_ai_runtime["direct_path"],
                "rag_path": private_ai_runtime["rag_path"],
                "request_shape": private_ai_runtime["request_shape"],
                "direct_model": private_ai_runtime["direct_model"],
                "rag_model": private_ai_runtime["rag_model"],
                "timeout_seconds": private_ai_runtime["timeout_seconds"],
            },
        ),
    ]

    warnings: list[str] = []
    private_ai = providers[0]
    if not private_ai["feature_enabled"]:
        warnings.append("private_ai_runtime is disabled")
    if not private_ai["diagnostics"]["base_url_configured"]:
        warnings.append("private_ai_runtime base URL is missing")
    if private_ai_runtime["primary_provider"] != "private_ai_runtime":
        warnings.append("provider_runtime primary provider is not private_ai_runtime")
    if not private_ai["diagnostics"]["token_file_configured"] and settings.app_env == "production":
        warnings.append("private_ai_runtime token file is missing")
    if settings.app_env == "production" and private_ai["diagnostics"]["inline_token_configured"]:
        warnings.append("private_ai_runtime inline token is forbidden in production")
    if private_ai_runtime["shape_mismatch"]:
        warnings.append("private_ai_runtime endpoint and request shape are incompatible")
    if private_ai_runtime["rag_isolation_error"]:
        warnings.append("private_ai_runtime RAG model requires an isolated runtime")

    return {
        "ok": not warnings,
        "status": "ready" if not warnings else "warning",
        "app_env": settings.app_env,
        "webchat_runtime_enabled": settings.enabled,
        "configured_provider": "private_ai_runtime",
        "fallback_provider": None,
        "human_webcall": get_human_webcall_runtime_status(db),
        "webcall_ai_demo_lab": get_demo_lab_status(db),
        "providers": providers,
        "warnings": warnings,
        "boundary": {
            "secret_values_exposed": False,
            "external_network_call": False,
            "customer_message_sent": False,
        },
    }


def _safe_path(value: str) -> str:
    return urlparse(value or "").path or "/"


def _same_runtime_origin(left: str, right: str) -> bool:
    left_parsed = urlparse(left or "")
    right_parsed = urlparse(right or "")
    return (
        left_parsed.scheme.lower(),
        left_parsed.hostname or "",
        left_parsed.port,
    ) == (
        right_parsed.scheme.lower(),
        right_parsed.hostname or "",
        right_parsed.port,
    )


def _known_endpoint_shape_mismatch(path: str, request_shape: str, *, endpoint_kind: str) -> str | None:
    del endpoint_kind
    normalized_path = _safe_path(path).rstrip("/") or "/"
    if normalized_path == "/api/chat" and request_shape != "ollama_chat":
        return "endpoint_request_shape_mismatch"
    if normalized_path in {"/chat/direct", "/chat/rag"} and request_shape != "question":
        return "endpoint_request_shape_mismatch"
    return None
