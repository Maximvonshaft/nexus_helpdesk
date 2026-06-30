from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.settings import get_settings  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings  # noqa: E402


def _outbound_email_successful_test_send_count(database_url: str, *, max_age_hours: int) -> int:
    engine = create_engine(database_url)
    cutoff = utc_now() - timedelta(hours=max_age_hours)
    try:
        with engine.connect() as conn:
            return int(conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM outbound_email_accounts
                    WHERE is_active = true
                      AND health_status = 'ok'
                      AND last_test_status = 'success'
                      AND last_test_at IS NOT NULL
                      AND last_test_at >= :cutoff
                    """
                ),
                {"cutoff": cutoff},
            ).scalar() or 0)
    finally:
        engine.dispose()


def main() -> int:
    settings = get_settings()
    warnings: list[str] = []
    if not settings.is_postgres:
        warnings.append("DATABASE_URL is not PostgreSQL")
    if settings.storage_backend == "local":
        warnings.append("STORAGE_BACKEND is local")
    if settings.openclaw_transport != "disabled":
        warnings.append("OPENCLAW_TRANSPORT must remain disabled; legacy OpenClaw runtime is retired")
    if settings.openclaw_deployment_mode != "disabled":
        warnings.append("OPENCLAW_DEPLOYMENT_MODE must remain disabled; legacy OpenClaw runtime is retired")
    if settings.openclaw_bridge_enabled:
        warnings.append("OPENCLAW_BRIDGE_ENABLED must remain false; legacy OpenClaw bridge is retired")
    if settings.openclaw_cli_fallback_enabled:
        warnings.append("OPENCLAW_CLI_FALLBACK_ENABLED must be false for production")
    if settings.openclaw_sync_enabled:
        warnings.append("OPENCLAW_SYNC_ENABLED must remain false; legacy OpenClaw sync is retired")
    if settings.openclaw_event_driver_enabled:
        warnings.append("OPENCLAW_EVENT_DRIVER_ENABLED must remain false; legacy OpenClaw event driver is retired")
    if settings.metrics_enabled and not settings.metrics_token:
        warnings.append("METRICS_ENABLED=true but METRICS_TOKEN is missing")
    if settings.openclaw_attachment_url_fetch_enabled and not settings.openclaw_attachment_allowed_hosts:
        warnings.append("OPENCLAW_ATTACHMENT_URL_FETCH_ENABLED=true but OPENCLAW_ATTACHMENT_ALLOWED_HOSTS is empty")
    if settings.app_env == "production" and not settings.webchat_allowed_origins:
        warnings.append("WEBCHAT_ALLOWED_ORIGINS is empty; public webchat will reject browser origins")
    if settings.app_env == "production" and settings.webchat_rate_limit_backend != "database":
        warnings.append("WEBCHAT_RATE_LIMIT_BACKEND should be database in production")
    if settings.app_env == "production" and settings.webchat_ai_auto_reply_mode not in {"off", "safe_ack"}:
        warnings.append("WEBCHAT_AI_AUTO_REPLY_MODE should be off or safe_ack in production")
    if settings.webchat_allow_legacy_token_transport:
        warnings.append("WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must remain false")
    outbound_email_successful_test_send_accounts = 0
    if settings.outbound_email_production_pilot_enabled:
        try:
            outbound_email_successful_test_send_accounts = _outbound_email_successful_test_send_count(
                settings.database_url,
                max_age_hours=settings.outbound_email_test_send_max_age_hours,
            )
        except Exception as exc:
            warnings.append(f"Outbound Email production pilot test-send gate failed: {exc.__class__.__name__}")
        if outbound_email_successful_test_send_accounts < 1:
            warnings.append(
                f"OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true requires one active SMTP account with successful test-send in the last {settings.outbound_email_test_send_max_age_hours} hours"
            )
    try:
        webcall_ai = get_webcall_ai_production_settings()
    except Exception as exc:
        warnings.append(f"WebCall AI production config invalid: {exc}")
        webcall_ai = None
    if webcall_ai is not None and webcall_ai.production_enabled:
        if webcall_ai.record_raw_audio:
            warnings.append("WEBCALL_AI_RECORD_RAW_AUDIO must remain false")
        if webcall_ai.webchat_voice_provider != "livekit":
            warnings.append("WEBCALL_AI_PRODUCTION_ENABLED requires WEBCHAT_VOICE_PROVIDER=livekit")
        if "/webcall-ai" not in os.getenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", ""):
            warnings.append("WEBCALL_AI_PRODUCTION_ENABLED requires WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES to include /webcall-ai")
        if webcall_ai.livekit_url and webcall_ai.livekit_url not in os.getenv("WEBCHAT_VOICE_CONNECT_SRC", ""):
            warnings.append("WEBCALL_AI_PRODUCTION_ENABLED requires WEBCHAT_VOICE_CONNECT_SRC to include the LiveKit URL")
        if not webcall_ai.livekit_configured:
            warnings.append("WEBCALL_AI_PRODUCTION_ENABLED requires LiveKit URL/key/secret in runtime env")
        if webcall_ai.allow_speedaf_work_order or webcall_ai.allow_cancel or webcall_ai.allow_address_update:
            warnings.append("high-risk WebCall AI actions must remain disabled for production rollout")
    payload = {
        "app_env": settings.app_env,
        "database_url_scheme": settings.database_url.split(":", 1)[0],
        "is_postgres": settings.is_postgres,
        "storage_backend": settings.storage_backend,
        "openclaw_transport": settings.openclaw_transport,
        "openclaw_deployment_mode": settings.openclaw_deployment_mode,
        "openclaw_bridge_enabled": settings.openclaw_bridge_enabled,
        "openclaw_bridge_url_configured": bool(settings.openclaw_bridge_url),
        "openclaw_cli_fallback_enabled": settings.openclaw_cli_fallback_enabled,
        "metrics_enabled": settings.metrics_enabled,
        "metrics_token_configured": bool(settings.metrics_token),
        "openclaw_sync_enabled": settings.openclaw_sync_enabled,
        "openclaw_event_driver_enabled": settings.openclaw_event_driver_enabled,
        "openclaw_attachment_url_fetch_enabled": settings.openclaw_attachment_url_fetch_enabled,
        "openclaw_attachment_allowed_hosts": settings.openclaw_attachment_allowed_hosts,
        "webchat_allowed_origins_configured": bool(settings.webchat_allowed_origins),
        "webchat_allow_legacy_token_transport": settings.webchat_allow_legacy_token_transport,
        "webchat_rate_limit_backend": settings.webchat_rate_limit_backend,
        "webchat_ai_auto_reply_mode": settings.webchat_ai_auto_reply_mode,
        "outbound_email_production_pilot_enabled": settings.outbound_email_production_pilot_enabled,
        "outbound_email_successful_test_send_accounts": outbound_email_successful_test_send_accounts,
        "outbound_email_test_send_max_age_hours": settings.outbound_email_test_send_max_age_hours,
        "warnings": warnings,
        "webcall_ai": None if webcall_ai is None else {
            "production_enabled": webcall_ai.production_enabled,
            "agent_enabled": webcall_ai.agent_enabled,
            "provider_profile": webcall_ai.provider_profile,
            "voice_provider": webcall_ai.webchat_voice_provider,
            "livekit_url_configured": bool(webcall_ai.livekit_url),
            "livekit_api_key_configured": webcall_ai.livekit_api_key_configured,
            "livekit_api_secret_configured": webcall_ai.livekit_api_secret_configured,
            "record_raw_audio": webcall_ai.record_raw_audio,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())
