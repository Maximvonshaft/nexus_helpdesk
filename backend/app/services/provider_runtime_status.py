from __future__ import annotations

import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .webchat_fast_config import get_webchat_fast_settings


HUMAN_WEBCALL_RINGING_STATUSES = {"created", "ringing"}
HUMAN_WEBCALL_ACTIVE_STATUSES = {"accepted", "active"}


def _provider_capabilities(*, webchat_fast_reply: bool, handoff_decision: bool = True) -> dict[str, bool]:
    return {
        "webchat_fast_reply": webchat_fast_reply,
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


def _credential_status(db: Session | None, tenant_id: str) -> dict[str, Any]:
    empty = {
        "active_credential_exists": False,
        "has_access": False,
        "has_refresh": False,
        "expires_at": None,
    }
    if db is None:
        return empty
    row = db.execute(text("""
        SELECT encrypted_access_token, encrypted_refresh_token, expires_at
        FROM provider_credentials
        WHERE tenant_id = :tenant_id
          AND provider = 'openai-codex'
          AND provider_runtime = 'codex_app_server'
          AND credential_type = 'oauth'
          AND status = 'active'
          AND revoked_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
    """), {"tenant_id": tenant_id}).mappings().first()
    if not row:
        return empty
    return {
        "active_credential_exists": True,
        "has_access": bool(row["encrypted_access_token"]),
        "has_refresh": bool(row["encrypted_refresh_token"]),
        "expires_at": row["expires_at"].isoformat() if hasattr(row["expires_at"], "isoformat") else row["expires_at"],
    }


def _route_rule_exists(db: Session | None, tenant_id: str) -> bool:
    if db is None:
        return False
    row = db.execute(text("""
        SELECT 1
        FROM provider_routing_rules
        WHERE tenant_id = :tenant_id
          AND channel_key = 'website'
          AND scenario = 'webchat_fast_reply'
          AND enabled = true
        LIMIT 1
    """), {"tenant_id": tenant_id}).first()
    return bool(row)


def _bridge_readiness_from_env() -> dict[str, Any]:
    mode = os.environ.get("CODEX_APP_SERVER_BRIDGE_MODE", "real").strip().lower() or "real"
    backend = os.environ.get("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", "").strip()
    real_upstream_configured = (
        mode == "real"
        and (
            bool(os.environ.get("CODEX_APP_SERVER_REAL_UPSTREAM_URL", "").strip())
            or os.environ.get("CODEX_APP_SERVER_BRIDGE_REAL_UPSTREAM_CONFIGURED", "").strip().lower() in {"1", "true", "yes", "on"}
        )
    )
    return {
        "bridge_mode": mode,
        "real_upstream_configured": real_upstream_configured,
        "reply_generation_backend": backend or ("stub" if mode == "stub" else "unconfigured"),
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
    except SQLAlchemyError as exc:
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
        settings = get_webchat_fast_settings()
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

    provider_runtime_tenant_id = "default"
    credential_diagnostics = _credential_status(db, provider_runtime_tenant_id)
    route_rule_exists = _route_rule_exists(db, provider_runtime_tenant_id)
    bridge_readiness = _bridge_readiness_from_env()

    providers = [
        _provider_entry(
            name="openclaw_responses",
            selected=settings.provider == "openclaw_responses",
            feature_enabled=True,
            configured=settings.is_openclaw_configured,
            runtime="private_responses_proxy",
            capabilities=_provider_capabilities(webchat_fast_reply=settings.is_openclaw_configured),
            diagnostics={
                "url_configured": bool(settings.openclaw_responses_url),
                "token_file_configured": bool(settings.openclaw_responses_token_file),
                "token_configured": bool(settings.token),
                "connect_timeout_ms": settings.openclaw_connect_timeout_ms,
                "read_timeout_ms": settings.openclaw_read_timeout_ms,
                "total_timeout_ms": settings.openclaw_total_timeout_ms,
            },
        ),
        _provider_entry(
            name="codex_app_server",
            selected=settings.provider in {"codex_app_server", "provider_runtime"},
            feature_enabled=settings.codex_app_server_enabled or settings.provider == "provider_runtime",
            configured=(
                settings.is_codex_app_server_configured and bridge_readiness["bridge_mode"] != "stub"
                if settings.provider == "codex_app_server"
                else bool(
                    credential_diagnostics["active_credential_exists"]
                    and settings.codex_app_server_bridge_url
                    and bridge_readiness["real_upstream_configured"]
                    and bridge_readiness["bridge_mode"] != "stub"
                )
            ),
            runtime="private_sidecar_provider",
            capabilities=_provider_capabilities(
                webchat_fast_reply=(
                    settings.is_codex_app_server_configured and bridge_readiness["bridge_mode"] != "stub"
                    if settings.provider == "codex_app_server"
                    else bool(
                        credential_diagnostics["active_credential_exists"]
                        and settings.codex_app_server_bridge_url
                        and bridge_readiness["real_upstream_configured"]
                        and bridge_readiness["bridge_mode"] != "stub"
                    )
                )
            ),
            controls={
                "canary_percent": settings.codex_app_server_canary_percent,
                "kill_switch": settings.codex_app_server_kill_switch,
                "fallback_provider": settings.fallback_provider,
            },
            diagnostics={
                **credential_diagnostics,
                "bridge_url_configured": bool(settings.codex_app_server_bridge_url),
                "login_url_configured": bool(os.environ.get("CODEX_APP_SERVER_LOGIN_URL", "").strip()),
                "route_rule_exists": route_rule_exists,
                **bridge_readiness,
                "token_file_configured": bool(settings.codex_app_server_token_file),
                "token_configured": bool(settings.codex_app_server_token),
                "timeout_ms": settings.codex_app_server_timeout_ms,
            },
        ),
        _provider_entry(
            name="openai_responses",
            selected=settings.provider == "openai_responses",
            feature_enabled=settings.openai_enabled,
            configured=settings.is_openai_configured,
            runtime="openai_responses_api",
            capabilities=_provider_capabilities(webchat_fast_reply=settings.is_openai_configured),
            diagnostics={
                "api_key_file_configured": bool(settings.openai_api_key_file),
                "api_key_configured": bool(settings.openai_token),
            },
        ),
        _provider_entry(
            name="codex_auth",
            selected=settings.provider == "codex_auth",
            feature_enabled=settings.codex_enabled,
            configured=settings.is_codex_configured,
            runtime="legacy_codex_auth_provider",
            capabilities=_provider_capabilities(webchat_fast_reply=settings.is_codex_configured),
            diagnostics={
                "token_file_configured": bool(settings.codex_auth_token_file),
                "token_configured": bool(settings.codex_token),
            },
        ),
    ]

    warnings: list[str] = []
    selected = next((item for item in providers if item["selected"]), None)
    if selected and not selected["configured"]:
        warnings.append(f"selected provider {selected['name']} is not configured")
    if settings.provider == "codex_app_server":
        if bridge_readiness["bridge_mode"] == "stub":
            warnings.append("codex_app_server bridge is in stub mode")
        if settings.codex_app_server_kill_switch:
            warnings.append("codex_app_server kill switch is active; traffic routes to openclaw_responses")
        if settings.codex_app_server_canary_percent < 100 and settings.fallback_provider != "openclaw_responses":
            warnings.append("codex_app_server canary below 100 requires openclaw_responses fallback for skipped traffic")
        if settings.fallback_provider == "openclaw_responses" and not settings.is_openclaw_configured:
            warnings.append("openclaw_responses fallback is selected but not configured")
    if settings.provider == "provider_runtime":
        codex = next(item for item in providers if item["name"] == "codex_app_server")
        if not codex["diagnostics"]["active_credential_exists"]:
            warnings.append("provider_runtime codex_app_server active credential is missing")
        if not settings.codex_app_server_bridge_url:
            warnings.append("provider_runtime codex_app_server bridge URL is missing")
        if codex["diagnostics"]["bridge_mode"] == "stub":
            warnings.append("provider_runtime codex_app_server bridge is in stub mode")
        if not codex["diagnostics"]["real_upstream_configured"]:
            warnings.append("provider_runtime codex_app_server real upstream is not configured")
        if not route_rule_exists:
            warnings.append("provider_runtime webchat_fast_reply route rule is missing")

    return {
        "ok": not warnings,
        "status": "ready" if not warnings else "warning",
        "app_env": settings.app_env,
        "fast_lane_enabled": settings.enabled,
        "configured_provider": settings.provider,
        "fallback_provider": settings.fallback_provider,
        "human_webcall": get_human_webcall_runtime_status(db),
        "providers": providers,
        "warnings": warnings,
        "boundary": {
            "secret_values_exposed": False,
            "external_network_call": False,
            "customer_message_sent": False,
        },
    }
