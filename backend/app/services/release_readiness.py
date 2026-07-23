from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import database_pool_snapshot
from ..livekit_agent_config import load_livekit_agent_worker_config
from ..settings import get_settings
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .queue_health import collect_queue_health
from .release_metadata import runtime_identity_status
from .storage_readiness import check_storage_readiness

settings = get_settings()

_PROFILE_VALUES = {"controlled", "provider_canary", "full"}
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_IMAGE_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name}_invalid")


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name}_invalid") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name}_out_of_range")
    return value


def _env_token(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip().lower()
    if len(value) > 80 or not re.fullmatch(r"[a-z0-9_.-]*", value):
        raise RuntimeError(f"{name}_invalid")
    return value


def _migration_snapshot(db: Session) -> dict[str, Any]:
    expected = settings.expected_migration_head
    try:
        rows = db.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).all()
    except Exception:
        return {
            "status": "not_ready",
            "expected": expected,
            "observed": None,
            "reason_codes": ["migration_head_unavailable"],
        }

    observed_heads = [str(row[0]).strip() for row in rows if row and row[0]]
    reason_codes: list[str] = []
    if len(observed_heads) != 1:
        reason_codes.append("migration_head_count_invalid")
    observed = observed_heads[0] if len(observed_heads) == 1 else None
    if expected and observed != expected:
        reason_codes.append("migration_head_mismatch")
    return {
        "status": "ready" if not reason_codes else "not_ready",
        "expected": expected,
        "observed": observed,
        "reason_codes": reason_codes,
    }


def _configuration_snapshot(profile: str) -> dict[str, Any]:
    provider_enabled = _env_bool("PROVIDER_RUNTIME_ENABLED", False)
    provider_mode = (
        _env_token("PROVIDER_RUNTIME_TRAFFIC_MODE", "control") or "control"
    )
    provider_kill_switch = _env_bool("PROVIDER_RUNTIME_KILL_SWITCH", True)
    provider_canary_percent = _env_int(
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        0,
        minimum=0,
        maximum=100,
    )
    outbound_enabled = _env_bool("ENABLE_OUTBOUND_DISPATCH", False)
    outbound_provider = _env_token("OUTBOUND_PROVIDER", "disabled") or "disabled"
    webchat_ai_enabled = _env_bool("WEBCHAT_AI_ENABLED", False)
    human_call_enabled = _env_bool("WEBCHAT_HUMAN_CALL_ENABLED", False)
    live_ai_voice_enabled = _env_bool("WEBCHAT_LIVE_AI_VOICE_ENABLED", False)
    voice_enabled = human_call_enabled or live_ai_voice_enabled
    operations_mode = (
        _env_token("OPERATIONS_DISPATCH_MODE", "disabled") or "disabled"
    )

    reason_codes: list[str] = []
    if profile == "controlled":
        if provider_enabled:
            reason_codes.append("controlled_provider_enabled")
        if provider_mode != "control":
            reason_codes.append("controlled_provider_mode_not_control")
        if not provider_kill_switch:
            reason_codes.append("controlled_provider_kill_switch_inactive")
        if provider_canary_percent != 0:
            reason_codes.append("controlled_provider_canary_nonzero")
        if webchat_ai_enabled:
            reason_codes.append("controlled_webchat_ai_enabled")
        if voice_enabled:
            reason_codes.append("controlled_voice_enabled")
        if outbound_enabled or outbound_provider != "disabled":
            reason_codes.append("controlled_outbound_enabled")
        if operations_mode != "disabled":
            reason_codes.append("controlled_operations_enabled")
    elif profile == "provider_canary":
        if not provider_enabled:
            reason_codes.append("canary_provider_disabled")
        if provider_mode != "canary":
            reason_codes.append("canary_provider_mode_invalid")
        if provider_kill_switch:
            reason_codes.append("canary_provider_kill_switch_active")
        if not 1 <= provider_canary_percent <= 25:
            reason_codes.append("canary_percent_outside_approved_range")
        if outbound_enabled or outbound_provider != "disabled":
            reason_codes.append("canary_outbound_must_remain_disabled")
        if voice_enabled:
            reason_codes.append("canary_voice_must_remain_disabled")
        if operations_mode != "disabled":
            reason_codes.append("canary_operations_must_remain_disabled")
    elif profile == "full":
        if not provider_enabled:
            reason_codes.append("full_provider_disabled")
        if provider_mode != "full":
            reason_codes.append("full_provider_mode_invalid")
        if provider_kill_switch:
            reason_codes.append("full_provider_kill_switch_active")
        if provider_canary_percent != 100:
            reason_codes.append("full_provider_percent_not_100")
        if webchat_ai_enabled and not provider_enabled:
            reason_codes.append("full_webchat_ai_without_provider")
        if outbound_enabled and outbound_provider == "disabled":
            reason_codes.append("full_outbound_provider_disabled")

    return {
        "status": "ready" if not reason_codes else "not_ready",
        "reason_codes": reason_codes,
        "provider": {
            "enabled": provider_enabled,
            "mode": provider_mode,
            "kill_switch": provider_kill_switch,
            "canary_percent": provider_canary_percent,
        },
        "outbound": {
            "enabled": outbound_enabled,
            "provider": outbound_provider,
        },
        "webchat_ai_enabled": webchat_ai_enabled,
        "voice_enabled": voice_enabled,
        "human_call_enabled": human_call_enabled,
        "live_ai_voice_enabled": live_ai_voice_enabled,
        "operations_mode": operations_mode,
        "contains_secrets": False,
    }


def _telephony_snapshot(db: Session) -> dict[str, Any]:
    human_call_enabled = _env_bool("WEBCHAT_HUMAN_CALL_ENABLED", False)
    live_ai_voice_enabled = _env_bool("WEBCHAT_LIVE_AI_VOICE_ENABLED", False)
    enabled = human_call_enabled or live_ai_voice_enabled
    if not enabled:
        return {
            "status": "ready",
            "enabled": False,
            "reason_codes": [],
            "contains_secrets": False,
        }

    reason_codes: list[str] = []
    voice = None
    media_worker_ready = False
    try:
        voice = load_webchat_voice_runtime_config()
    except Exception:
        reason_codes.append("voice_runtime_configuration_invalid")

    if voice is not None:
        if voice.provider != "livekit":
            reason_codes.append("voice_provider_not_livekit")
        if not voice.livekit_webhook_enabled:
            reason_codes.append("voice_webhook_disabled")
        if voice.live_ai_voice_enabled:
            try:
                load_livekit_agent_worker_config()
                media_worker_ready = True
            except Exception:
                reason_codes.append("voice_media_worker_configuration_invalid")

    channel_counts = {
        "enabled_channels": 0,
        "inbound_ready_channels": 0,
        "outbound_ready_channels": 0,
        "invalid_ai_first_channels": 0,
    }
    try:
        row = db.execute(
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
        channel_counts = {key: int(row[key] or 0) for key in channel_counts}
    except Exception:
        reason_codes.append("voice_channel_readiness_unavailable")

    if channel_counts["enabled_channels"] < 1:
        reason_codes.append("voice_channel_not_enabled")
    if channel_counts["inbound_ready_channels"] < 1:
        reason_codes.append("voice_inbound_route_not_ready")
    if channel_counts["invalid_ai_first_channels"]:
        reason_codes.append("voice_ai_first_agent_missing")

    return {
        "status": "ready" if not reason_codes else "not_ready",
        "enabled": True,
        "human_call_enabled": human_call_enabled,
        "live_ai_voice_enabled": live_ai_voice_enabled,
        "provider": voice.provider if voice is not None else None,
        "routing_mode": voice.routing_mode if voice is not None else None,
        "webhook_enabled": bool(voice and voice.livekit_webhook_enabled),
        "media_worker_ready": media_worker_ready,
        "reason_codes": sorted(set(reason_codes)),
        "contains_secrets": False,
        **channel_counts,
    }


def _identity_snapshot() -> dict[str, Any]:
    identity = runtime_identity_status(default_app_version=settings.app_version)
    source_sha = str(
        identity.get("git_sha") or os.getenv("GIT_SHA", "")
    ).strip().lower()
    frontend_sha = str(
        identity.get("frontend_build_sha")
        or os.getenv("FRONTEND_BUILD_SHA", "")
    ).strip().lower()
    image = str(
        identity.get("image_tag") or os.getenv("IMAGE_TAG", "")
    ).strip().lower()

    reason_codes: list[str] = []
    if not _HEX40_RE.fullmatch(source_sha):
        reason_codes.append("source_sha_invalid")
    if not _HEX40_RE.fullmatch(frontend_sha):
        reason_codes.append("frontend_sha_invalid")
    if source_sha and frontend_sha and source_sha != frontend_sha:
        reason_codes.append("frontend_source_sha_mismatch")
    if not _DIGEST_IMAGE_RE.fullmatch(image):
        reason_codes.append("image_digest_invalid")
    if not identity.get("release_metadata_complete"):
        reason_codes.append("release_metadata_incomplete")
    return {
        "status": "ready" if not reason_codes else "not_ready",
        "reason_codes": reason_codes,
        "source_sha": source_sha or None,
        "frontend_sha": frontend_sha or None,
        "image": image or None,
        "app_version": identity.get("app_version"),
        "build_time": identity.get("build_time"),
    }


def evaluate_release_readiness(
    db: Session,
    *,
    profile: str = "controlled",
) -> dict[str, Any]:
    """Collect runtime facts without granting activation authority.

    Activation evidence and all authorization booleans are finalized exclusively
    by ``activation_evidence_policy.finalize_release_readiness``.
    """

    normalized_profile = profile.strip().lower()
    if normalized_profile not in _PROFILE_VALUES:
        raise ValueError("release_profile_invalid")

    identity = _identity_snapshot()
    migration = _migration_snapshot(db)
    configuration = _configuration_snapshot(normalized_profile)
    telephony = _telephony_snapshot(db)
    queue = collect_queue_health(db)
    storage = check_storage_readiness().as_dict()
    database_pool = database_pool_snapshot()

    reason_codes: list[str] = []
    for prefix, collector in (
        ("identity", identity),
        ("migration", migration),
        ("configuration", configuration),
        ("telephony", telephony),
        ("queue", queue),
        ("storage", storage),
    ):
        if collector.get("status") not in {"ready", "ok"}:
            collector_reasons = collector.get("reason_codes")
            if isinstance(collector_reasons, list) and collector_reasons:
                reason_codes.extend(
                    f"{prefix}:{str(code)[:160]}"
                    for code in collector_reasons
                )
            else:
                reason_codes.append(f"{prefix}:not_ready")

    return {
        "schema": "nexus.release-readiness.v2",
        "profile": normalized_profile,
        "status": "ready" if not reason_codes else "not_ready",
        "reason_codes": sorted(set(reason_codes)),
        "collectors": {
            "identity": identity,
            "migration": migration,
            "configuration": configuration,
            "telephony": telephony,
            "queue": queue,
            "storage": storage,
            "database_pool": database_pool,
        },
        "production_authorized": False,
        "provider_enablement_authorized": False,
        "webchat_ai_enablement_authorized": False,
        "voice_enablement_authorized": False,
        "outbound_enablement_authorized": False,
        "operations_enablement_authorized": False,
    }
