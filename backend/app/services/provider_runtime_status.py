from __future__ import annotations

from typing import Any

from .webchat_fast_config import get_webchat_fast_settings


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


def get_provider_runtime_status() -> dict[str, Any]:
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
            selected=settings.provider == "codex_app_server",
            feature_enabled=settings.codex_app_server_enabled,
            configured=settings.is_codex_app_server_configured,
            runtime="private_sidecar_provider",
            capabilities=_provider_capabilities(webchat_fast_reply=settings.is_codex_app_server_configured),
            controls={
                "canary_percent": settings.codex_app_server_canary_percent,
                "kill_switch": settings.codex_app_server_kill_switch,
                "fallback_provider": settings.fallback_provider,
            },
            diagnostics={
                "bridge_url_configured": bool(settings.codex_app_server_bridge_url),
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
        if settings.codex_app_server_kill_switch:
            warnings.append("codex_app_server kill switch is active; traffic routes to openclaw_responses")
        if settings.codex_app_server_canary_percent < 100 and settings.fallback_provider != "openclaw_responses":
            warnings.append("codex_app_server canary below 100 requires openclaw_responses fallback for skipped traffic")
        if settings.fallback_provider == "openclaw_responses" and not settings.is_openclaw_configured:
            warnings.append("openclaw_responses fallback is selected but not configured")

    return {
        "ok": not warnings,
        "status": "ready" if not warnings else "warning",
        "app_env": settings.app_env,
        "fast_lane_enabled": settings.enabled,
        "configured_provider": settings.provider,
        "fallback_provider": settings.fallback_provider,
        "providers": providers,
        "warnings": warnings,
        "boundary": {
            "secret_values_exposed": False,
            "external_network_call": False,
            "customer_message_sent": False,
        },
    }
