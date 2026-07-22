from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import ChannelAccount, Market, User
from ..utils.time import utc_now
from ..voice_models import VoiceChannelConfiguration
from .audit_service import log_admin_audit
from .identity_tenant_scope import actor_tenant_id, ensure_resource_tenant

_ROUTING_MODES = {"ai_first", "human_first"}
_RECORDING_POLICIES = {"disabled", "consent_required", "always"}
_TRANSCRIPTION_POLICIES = {"disabled", "consent_required", "always"}
_OVERFLOW_ACTIONS = {"ai", "voicemail", "disconnect"}


def _clean_optional(value: str | None, *, limit: int) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:limit] or None


def _validate_timezone(value: str) -> str:
    normalized = str(value or "UTC").strip() or "UTC"
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid voice timezone",
        ) from exc
    return normalized


def _validate_business_hours(value: dict[str, Any] | None) -> str | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid voice business hours",
        )
    allowed_days = {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    }
    normalized: dict[str, list[dict[str, str]]] = {}
    for day, windows in value.items():
        day_key = str(day or "").strip().lower()
        if day_key not in allowed_days or not isinstance(windows, list):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid voice business hours",
            )
        normalized_windows: list[dict[str, str]] = []
        for window in windows:
            if not isinstance(window, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="invalid voice business hours",
                )
            start = str(window.get("start") or "").strip()
            end = str(window.get("end") or "").strip()
            if len(start) != 5 or len(end) != 5 or start[2] != ":" or end[2] != ":":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="invalid voice business hours",
                )
            try:
                start_minutes = int(start[:2]) * 60 + int(start[3:])
                end_minutes = int(end[:2]) * 60 + int(end[3:])
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="invalid voice business hours",
                ) from exc
            if not (0 <= start_minutes < 24 * 60 and 0 <= end_minutes <= 24 * 60):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="invalid voice business hours",
                )
            if start_minutes >= end_minutes:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="voice business-hour window must end after it starts",
                )
            normalized_windows.append({"start": start, "end": end})
        normalized[day_key] = normalized_windows
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _business_hours(row: VoiceChannelConfiguration | None) -> dict[str, Any] | None:
    if row is None or not row.business_hours_json:
        return None
    try:
        value = json.loads(row.business_hours_json)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def serialize_voice_configuration(
    row: VoiceChannelConfiguration | None,
    account: ChannelAccount,
) -> dict[str, Any]:
    return {
        "id": row.id if row is not None else None,
        "channel_account_id": account.id,
        "account_id": account.account_id,
        "phone_number": account.account_id,
        "display_name": account.display_name,
        "market_id": account.market_id,
        "tenant_id": account.tenant_id,
        "health_status": account.health_status,
        "livekit_project_ref": row.livekit_project_ref if row is not None else None,
        "inbound_trunk_id": row.inbound_trunk_id if row is not None else None,
        "outbound_trunk_id": row.outbound_trunk_id if row is not None else None,
        "dispatch_rule_id": row.dispatch_rule_id if row is not None else None,
        "routing_mode": row.routing_mode if row is not None else "ai_first",
        "ai_agent_name": row.ai_agent_name if row is not None else None,
        "timezone": row.timezone if row is not None else "UTC",
        "business_hours": _business_hours(row),
        "queue_timeout_seconds": row.queue_timeout_seconds if row is not None else 90,
        "offer_timeout_seconds": row.offer_timeout_seconds if row is not None else 20,
        "wrap_up_seconds": row.wrap_up_seconds if row is not None else 30,
        "overflow_action": row.overflow_action if row is not None else "ai",
        "voicemail_enabled": bool(row.voicemail_enabled) if row is not None else False,
        "recording_policy": row.recording_policy if row is not None else "disabled",
        "transcription_policy": row.transcription_policy if row is not None else "disabled",
        "enabled": bool(row.enabled) if row is not None else False,
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
    }


def list_voice_configurations(
    db: Session,
    *,
    actor: User,
) -> list[dict[str, Any]]:
    tenant_id = actor_tenant_id(db, actor)
    rows = (
        db.query(ChannelAccount, VoiceChannelConfiguration)
        .outerjoin(
            VoiceChannelConfiguration,
            VoiceChannelConfiguration.channel_account_id == ChannelAccount.id,
        )
        .filter(
            ChannelAccount.provider == "voice",
            ChannelAccount.tenant_id == tenant_id,
        )
        .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
        .all()
    )
    return [serialize_voice_configuration(configuration, account) for account, configuration in rows]


def _tenant_voice_account(
    db: Session,
    *,
    actor: User,
    channel_account_id: int,
    lock: bool,
) -> ChannelAccount:
    tenant_id = actor_tenant_id(db, actor)
    query = db.query(ChannelAccount).filter(
        ChannelAccount.id == channel_account_id,
        ChannelAccount.provider == "voice",
        ChannelAccount.tenant_id == tenant_id,
    )
    if lock and db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    account = query.first()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="voice channel account not found",
        )
    ensure_resource_tenant(
        db,
        active_tenant_id=tenant_id,
        resource=account,
        resource_kind="channel_account",
        allow_shadow=False,
    )
    if account.market_id is not None:
        market = db.get(Market, account.market_id)
        if market is None or market.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="voice channel market tenant mismatch",
            )
    return account


def upsert_voice_configuration(
    db: Session,
    *,
    actor: User,
    channel_account_id: int,
    livekit_project_ref: str | None,
    inbound_trunk_id: str | None,
    outbound_trunk_id: str | None,
    dispatch_rule_id: str | None,
    routing_mode: str,
    ai_agent_name: str | None,
    timezone: str,
    business_hours: dict[str, Any] | None,
    queue_timeout_seconds: int,
    offer_timeout_seconds: int,
    wrap_up_seconds: int,
    overflow_action: str,
    voicemail_enabled: bool,
    recording_policy: str,
    transcription_policy: str,
    enabled: bool,
) -> dict[str, Any]:
    account = _tenant_voice_account(
        db,
        actor=actor,
        channel_account_id=channel_account_id,
        lock=True,
    )
    normalized_routing = str(routing_mode or "").strip().lower()
    normalized_recording = str(recording_policy or "").strip().lower()
    normalized_transcription = str(transcription_policy or "").strip().lower()
    normalized_overflow = str(overflow_action or "").strip().lower()
    normalized_agent = _clean_optional(ai_agent_name, limit=160)
    normalized_inbound = _clean_optional(inbound_trunk_id, limit=160)
    normalized_outbound = _clean_optional(outbound_trunk_id, limit=160)
    normalized_dispatch = _clean_optional(dispatch_rule_id, limit=160)
    normalized_project = _clean_optional(livekit_project_ref, limit=160)
    normalized_timezone = _validate_timezone(timezone)
    business_hours_json = _validate_business_hours(business_hours)

    if normalized_routing not in _ROUTING_MODES:
        raise HTTPException(status_code=422, detail="invalid voice routing mode")
    if normalized_recording not in _RECORDING_POLICIES:
        raise HTTPException(status_code=422, detail="invalid voice recording policy")
    if normalized_transcription not in _TRANSCRIPTION_POLICIES:
        raise HTTPException(status_code=422, detail="invalid voice transcription policy")
    if normalized_overflow not in _OVERFLOW_ACTIONS:
        raise HTTPException(status_code=422, detail="invalid voice overflow action")
    if not 15 <= int(queue_timeout_seconds) <= 3600:
        raise HTTPException(status_code=422, detail="invalid voice queue timeout")
    if not 5 <= int(offer_timeout_seconds) <= 120:
        raise HTTPException(status_code=422, detail="invalid voice offer timeout")
    if not 0 <= int(wrap_up_seconds) <= 900:
        raise HTTPException(status_code=422, detail="invalid voice wrap-up")
    if enabled and not normalized_inbound:
        raise HTTPException(
            status_code=422,
            detail="inbound trunk is required for an enabled voice channel",
        )
    if enabled and not normalized_dispatch:
        raise HTTPException(
            status_code=422,
            detail="dispatch rule is required for an enabled voice channel",
        )
    if enabled and not normalized_agent:
        raise HTTPException(
            status_code=422,
            detail="LiveKit Agent name is required for telephony control",
        )
    if normalized_overflow == "voicemail" and not voicemail_enabled:
        raise HTTPException(
            status_code=422,
            detail="voicemail must be enabled for voicemail overflow",
        )

    row_query = db.query(VoiceChannelConfiguration).filter(
        VoiceChannelConfiguration.channel_account_id == account.id
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        row_query = row_query.with_for_update()
    row = row_query.first()
    old_value = serialize_voice_configuration(row, account) if row is not None else None
    now = utc_now()
    if row is None:
        row = VoiceChannelConfiguration(
            channel_account_id=account.id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    row.livekit_project_ref = normalized_project
    row.inbound_trunk_id = normalized_inbound
    row.outbound_trunk_id = normalized_outbound
    row.dispatch_rule_id = normalized_dispatch
    row.routing_mode = normalized_routing
    row.ai_agent_name = normalized_agent
    row.timezone = normalized_timezone
    row.business_hours_json = business_hours_json
    row.queue_timeout_seconds = int(queue_timeout_seconds)
    row.offer_timeout_seconds = int(offer_timeout_seconds)
    row.wrap_up_seconds = int(wrap_up_seconds)
    row.overflow_action = normalized_overflow
    row.voicemail_enabled = bool(voicemail_enabled)
    row.recording_policy = normalized_recording
    row.transcription_policy = normalized_transcription
    row.enabled = bool(enabled)
    row.updated_at = now
    account.health_status = "configured" if row.enabled else "disabled"
    account.updated_at = now
    db.flush()
    result = serialize_voice_configuration(row, account)
    log_admin_audit(
        db,
        actor_id=actor.id,
        action="telephony.voice_configuration.updated",
        target_type="voice_channel_configuration",
        target_id=row.id,
        old_value=old_value,
        new_value=result,
    )
    return result
