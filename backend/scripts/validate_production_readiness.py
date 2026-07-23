from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.services.release_readiness import evaluate_release_readiness  # noqa: E402
from app.services.storage_readiness import check_storage_readiness  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


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


def _storage_warnings(storage: dict) -> list[str]:
    warnings: list[str] = []
    if storage.get("status") in {"ok", "ready"}:
        return warnings
    for issue in list(storage.get("errors") or []) + list(
        storage.get("warnings") or []
    ):
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "storage_not_ready")
        warnings.append(f"Storage readiness: {code}")
    if not warnings:
        warnings.append("Storage readiness: not_ready")
    return warnings


def main() -> int:
    settings = get_settings()
    warnings: list[str] = []
    if not settings.is_postgres:
        warnings.append("DATABASE_URL is not PostgreSQL")
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
        warnings.append(
            "WEBCHAT_AI_AUTO_REPLY_MODE should be off or runtime in production"
        )
    if settings.webchat_allow_legacy_token_transport:
        warnings.append("WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must remain false")

    storage = check_storage_readiness(settings).as_dict()
    warnings.extend(_storage_warnings(storage))

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

    profile = os.getenv("PRODUCTION_PROFILE", "full").strip().lower() or "full"
    release_readiness: dict = {
        "schema": "nexus.release-readiness.v2",
        "profile": profile,
        "status": "not_ready",
        "reason_codes": ["release_readiness_unavailable"],
        "collectors": {},
        "production_authorized": False,
        "provider_enablement_authorized": False,
        "webchat_ai_enablement_authorized": False,
        "voice_enablement_authorized": False,
        "outbound_enablement_authorized": False,
        "operations_enablement_authorized": False,
    }
    db = SessionLocal()
    try:
        release_readiness = evaluate_release_readiness(db, profile=profile)
    except Exception as exc:
        warnings.append(
            "Release readiness evaluation failed: "
            f"{exc.__class__.__name__}"
        )
    finally:
        db.close()

    if release_readiness.get("status") != "ready":
        for code in release_readiness.get("reason_codes") or ["release_not_ready"]:
            warnings.append(f"Release readiness: {code}")
    if profile == "full" and not release_readiness.get("production_authorized"):
        warnings.append("Full production activation is not authorized")

    telephony = dict(
        (release_readiness.get("collectors") or {}).get("telephony")
        or {
            "status": "not_ready",
            "enabled": False,
            "reason_codes": ["telephony_readiness_unavailable"],
        }
    )

    payload = {
        "app_env": settings.app_env,
        "database_url_scheme": settings.database_url.split(":", 1)[0],
        "is_postgres": settings.is_postgres,
        "storage_backend": settings.storage_backend,
        "storage_readiness": storage,
        "metrics_enabled": settings.metrics_enabled,
        "webchat_allowed_origins_configured": bool(settings.webchat_allowed_origins),
        "webchat_allow_legacy_token_transport": (
            settings.webchat_allow_legacy_token_transport
        ),
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
        "release_readiness": release_readiness,
        "telephony": telephony,
        "warnings": sorted(set(warnings)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())
