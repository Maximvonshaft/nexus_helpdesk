from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.livekit_agent_config import load_livekit_agent_worker_config  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_voice_config import load_webchat_voice_runtime_config  # noqa: E402


def _outbound_email_successful_test_send_count(
    database_url: str,
    *,
    max_age_hours: int,
) -> int:
    engine = create_engine(database_url)
    cutoff = utc_now() - timedelta(hours=max_age_hours)
    try:
        with engine.connect() as conn:
            return int(
                conn.execute(
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
                ).scalar()
                or 0
            )
    finally:
        engine.dispose()


def _canonical_voice_channel_readiness(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                      COUNT(*) AS enabled_channels,
                      SUM(CASE
                        WHEN NULLIF(TRIM(v.inbound_trunk_id), '') IS NOT NULL
                         AND NULLIF(TRIM(v.dispatch_rule_id), '') IS NOT NULL
                        THEN 1 ELSE 0 END
                      ) AS inbound_ready_channels,
                      SUM(CASE
                        WHEN NULLIF(TRIM(v.outbound_trunk_id), '') IS NOT NULL
                        THEN 1 ELSE 0 END
                      ) AS outbound_ready_channels,
                      SUM(CASE
                        WHEN v.routing_mode = 'ai_first'
                         AND NULLIF(TRIM(v.ai_agent_name), '') IS NULL
                        THEN 1 ELSE 0 END
                      ) AS invalid_ai_first_channels
                    FROM channel_accounts AS c
                    JOIN voice_channel_configurations AS v
                      ON v.channel_account_id = c.id
                    WHERE c.provider = 'voice'
                      AND c.is_active = true
                      AND v.enabled = true
                    """
                )
            ).mappings().one()
            return {
                "enabled_channels": int(row["enabled_channels"] or 0),
                "inbound_ready_channels": int(row["inbound_ready_channels"] or 0),
                "outbound_ready_channels": int(row["outbound_ready_channels"] or 0),
                "invalid_ai_first_channels": int(row["invalid_ai_first_channels"] or 0),
            }
    finally:
        engine.dispose()


def main() -> int:
    settings = get_settings()
    warnings: list[str] = []
    if not settings.is_postgres:
        warnings.append("DATABASE_URL is not PostgreSQL")
    if settings.storage_backend == "local":
        warnings.append("STORAGE_BACKEND is local")
    if settings.metrics_enabled and not settings.metrics_token:
        warnings.append("METRICS_ENABLED=true but METRICS_TOKEN is missing")
    if settings.app_env == "production" and not settings.webchat_allowed_origins:
        warnings.append(
            "WEBCHAT_ALLOWED_ORIGINS is empty; public webchat will reject browser origins"
        )
    if (
        settings.app_env == "production"
        and settings.webchat_rate_limit_backend != "database"
    ):
        warnings.append("WEBCHAT_RATE_LIMIT_BACKEND should be database in production")
    if (
        settings.app_env == "production"
        and settings.webchat_ai_auto_reply_mode not in {"off", "runtime"}
    ):
        warnings.append("WEBCHAT_AI_AUTO_REPLY_MODE should be off or runtime in production")
    if settings.webchat_allow_legacy_token_transport:
        warnings.append("WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must remain false")

    outbound_email_successful_test_send_accounts = 0
    if settings.outbound_email_production_pilot_enabled:
        try:
            outbound_email_successful_test_send_accounts = (
                _outbound_email_successful_test_send_count(
                    settings.database_url,
                    max_age_hours=settings.outbound_email_test_send_max_age_hours,
                )
            )
        except Exception as exc:
            warnings.append(
                "Outbound Email production pilot test-send gate failed: "
                f"{exc.__class__.__name__}"
            )
        if outbound_email_successful_test_send_accounts < 1:
            warnings.append(
                "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true requires one active SMTP "
                "account with successful test-send in the last "
                f"{settings.outbound_email_test_send_max_age_hours} hours"
            )

    voice = None
    voice_worker_ready = False
    voice_channels = {
        "enabled_channels": 0,
        "inbound_ready_channels": 0,
        "outbound_ready_channels": 0,
        "invalid_ai_first_channels": 0,
    }
    telephony_reason_codes: list[str] = []
    try:
        voice = load_webchat_voice_runtime_config()
    except Exception:
        telephony_reason_codes.append("voice_runtime_configuration_invalid")

    if voice is not None and voice.enabled:
        if voice.provider != "livekit":
            telephony_reason_codes.append("voice_provider_not_livekit")
        if not voice.livekit_webhook_enabled:
            telephony_reason_codes.append("voice_webhook_disabled")
        if voice.live_ai_voice_enabled:
            try:
                load_livekit_agent_worker_config()
                voice_worker_ready = True
            except Exception:
                telephony_reason_codes.append("voice_media_worker_configuration_invalid")
        try:
            voice_channels = _canonical_voice_channel_readiness(settings.database_url)
        except Exception:
            telephony_reason_codes.append("voice_channel_readiness_unavailable")
        if voice_channels["enabled_channels"] < 1:
            telephony_reason_codes.append("voice_channel_not_enabled")
        if voice_channels["inbound_ready_channels"] < 1:
            telephony_reason_codes.append("voice_inbound_route_not_ready")
        if voice_channels["invalid_ai_first_channels"]:
            telephony_reason_codes.append("voice_ai_first_agent_missing")

    warnings.extend(f"Telephony readiness: {code}" for code in telephony_reason_codes)
    telephony_enabled = bool(voice and voice.enabled)
    telephony_ready = bool(
        voice is not None
        and not telephony_reason_codes
        and (
            not voice.live_ai_voice_enabled
            or voice_worker_ready
        )
    )

    payload = {
        "app_env": settings.app_env,
        "database_url_scheme": settings.database_url.split(":", 1)[0],
        "is_postgres": settings.is_postgres,
        "storage_backend": settings.storage_backend,
        "metrics_enabled": settings.metrics_enabled,
        "webchat_allowed_origins_configured": bool(settings.webchat_allowed_origins),
        "webchat_allow_legacy_token_transport": settings.webchat_allow_legacy_token_transport,
        "webchat_rate_limit_backend": settings.webchat_rate_limit_backend,
        "webchat_ai_auto_reply_mode": settings.webchat_ai_auto_reply_mode,
        "outbound_email_production_pilot_enabled": (
            settings.outbound_email_production_pilot_enabled
        ),
        "outbound_email_successful_test_send_accounts": (
            outbound_email_successful_test_send_accounts
        ),
        "outbound_email_test_send_max_age_hours": (
            settings.outbound_email_test_send_max_age_hours
        ),
        "telephony": {
            "enabled": telephony_enabled,
            "ready": telephony_ready,
            "human_call_enabled": bool(voice and voice.human_call_enabled),
            "live_ai_voice_enabled": bool(voice and voice.live_ai_voice_enabled),
            "provider": voice.provider if voice is not None else None,
            "routing_mode": voice.routing_mode if voice is not None else None,
            "webhook_enabled": bool(voice and voice.livekit_webhook_enabled),
            "media_worker_ready": voice_worker_ready,
            "reason_codes": sorted(set(telephony_reason_codes)),
            **voice_channels,
        },
        "warnings": warnings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())
