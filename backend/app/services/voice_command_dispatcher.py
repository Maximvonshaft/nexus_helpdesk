from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import ChannelAccount
from ..utils.time import utc_now
from ..voice_models import (
    VoiceChannelConfiguration,
    WebchatVoiceParticipant,
    WebchatVoiceSession,
    WebchatVoiceSessionAction,
)
from ..webchat_models import WebchatEvent
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .livekit_voice_provider import LiveKitVoiceProvider
from .mock_voice_provider import MockVoiceProvider
from .observability import record_voice_provider_error
from .voice_command_crypto import open_voice_command_payload
from .voice_provider import VoiceProvider, VoiceProviderError

logger = logging.getLogger(__name__)

COMMAND_MAX_ATTEMPTS = 5
COMMAND_LEASE_SECONDS = 60


def _provider_for_session(session: WebchatVoiceSession) -> VoiceProvider:
    if session.provider == "mock":
        return MockVoiceProvider()
    if session.provider == "livekit":
        return LiveKitVoiceProvider.from_config(load_webchat_voice_runtime_config())
    raise VoiceProviderError(f"unsupported voice provider: {session.provider}")


def _outbound_trunk_id(db: Session, *, session: WebchatVoiceSession) -> str | None:
    if session.channel_account_id is None:
        return None
    configuration = (
        db.query(VoiceChannelConfiguration)
        .join(ChannelAccount, ChannelAccount.id == VoiceChannelConfiguration.channel_account_id)
        .filter(
            VoiceChannelConfiguration.channel_account_id == session.channel_account_id,
            VoiceChannelConfiguration.enabled.is_(True),
            ChannelAccount.provider == "voice",
        )
        .first()
    )
    return configuration.outbound_trunk_id if configuration is not None else None


def _caller_identity(db: Session, *, session: WebchatVoiceSession) -> str | None:
    leg = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.participant_type.in_(["caller", "visitor"]),
            WebchatVoiceParticipant.status.notin_(["ended", "left", "failed"]),
        )
        .order_by(WebchatVoiceParticipant.id.asc())
        .first()
    )
    return leg.provider_identity if leg is not None else session.provider_call_id


def _write_event(
    db: Session,
    *,
    session: WebchatVoiceSession,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    db.add(
        WebchatEvent(
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            event_type=event_type,
            payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            created_at=utc_now(),
        )
    )


def _provider_error_retryable(exc: VoiceProviderError) -> bool:
    text = str(exc).lower()
    permanent_markers = (
        "unsupported",
        "required",
        "invalid",
        "missing required configuration",
        "not configured",
    )
    return not any(marker in text for marker in permanent_markers)


def _claim_due_commands(
    db: Session,
    *,
    worker_id: str,
    limit: int,
) -> list[int]:
    now = utc_now()
    due = and_(
        WebchatVoiceSessionAction.status.in_(["requested", "retryable"]),
        or_(
            WebchatVoiceSessionAction.next_attempt_at.is_(None),
            WebchatVoiceSessionAction.next_attempt_at <= now,
        ),
    )
    stale = and_(
        WebchatVoiceSessionAction.status == "dispatching",
        or_(
            WebchatVoiceSessionAction.lease_expires_at.is_(None),
            WebchatVoiceSessionAction.lease_expires_at <= now,
        ),
    )
    query = (
        db.query(WebchatVoiceSessionAction)
        .filter(or_(due, stale))
        .order_by(WebchatVoiceSessionAction.created_at.asc())
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update(skip_locked=True)
    rows = query.limit(max(1, min(int(limit or 20), 100))).all()
    claimed_ids: list[int] = []
    for command in rows:
        command.status = "dispatching"
        command.provider_status = "dispatching"
        command.provider_reason = None
        command.attempt_count += 1
        command.last_attempt_at = now
        command.lease_owner = worker_id[:120]
        command.lease_expires_at = now + timedelta(seconds=COMMAND_LEASE_SECONDS)
        command.updated_at = now
        claimed_ids.append(command.id)
    db.commit()
    return claimed_ids


def _result_payload(command: WebchatVoiceSessionAction) -> dict[str, Any]:
    try:
        result = json.loads(command.result_json or "{}")
    except json.JSONDecodeError:
        result = {}
    return result if isinstance(result, dict) else {}


def _finish(
    command: WebchatVoiceSessionAction,
    *,
    command_status: str,
    provider_status: str,
    provider_reason: str | None,
    provider_reference: str | None = None,
    provider_result: dict[str, Any] | None = None,
) -> None:
    now = utc_now()
    result = _result_payload(command)
    result["provider_result"] = provider_result or {}
    command.status = command_status
    command.provider_status = provider_status
    command.provider_reason = provider_reason
    command.provider_reference = provider_reference
    command.result_json = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    command.completed_at = (
        now if command_status in {"succeeded", "failed", "cancelled"} else None
    )
    command.next_attempt_at = None
    command.lease_owner = None
    command.lease_expires_at = None
    command.updated_at = now


def _retry(
    command: WebchatVoiceSessionAction,
    *,
    reason: str,
) -> None:
    command.status = "retryable"
    command.provider_status = "failed"
    command.provider_reason = reason
    command.next_attempt_at = utc_now() + timedelta(
        seconds=min(5 * (2 ** max(command.attempt_count - 1, 0)), 300)
    )
    command.lease_owner = None
    command.lease_expires_at = None
    command.updated_at = utc_now()


def _dispatch_one(db: Session, *, command_id: int) -> None:
    command = db.get(WebchatVoiceSessionAction, command_id)
    if command is None or command.status != "dispatching":
        return
    session = db.get(WebchatVoiceSession, command.voice_session_id)
    if session is None:
        _finish(
            command,
            command_status="failed",
            provider_status="failed",
            provider_reason="voice_session_missing",
        )
        db.commit()
        return
    if (
        session.status in {"ended", "missed", "failed", "cancelled"}
        and command.action_type != "hangup"
    ):
        _finish(
            command,
            command_status="cancelled",
            provider_status="cancelled",
            provider_reason="voice_session_closed",
        )
        db.commit()
        return

    payload = open_voice_command_payload(command.payload_json)
    try:
        result = _provider_for_session(session).execute_action(
            room_name=session.provider_room_name,
            action_type=command.action_type,
            target=payload.get("target"),
            digits=payload.get("digits"),
            participant_identity=_caller_identity(db, session=session),
            outbound_trunk_id=_outbound_trunk_id(db, session=session),
            idempotency_key=command.idempotency_key,
        )
    except VoiceProviderError as exc:
        retryable = _provider_error_retryable(exc)
        record_voice_provider_error(session.provider, command.action_type)
        logger.warning(
            "voice_command_provider_failed",
            extra={
                "voice_session_id": session.public_id,
                "command_id": command.public_id,
                "provider": session.provider,
                "action_type": command.action_type,
                "error_type": type(exc).__name__,
                "retryable": retryable,
            },
        )
        if retryable and command.attempt_count < COMMAND_MAX_ATTEMPTS:
            _retry(command, reason="provider_command_retryable")
        else:
            _finish(
                command,
                command_status="failed",
                provider_status="failed",
                provider_reason="provider_command_failed",
                provider_result={"retryable": False},
            )
        _write_event(
            db,
            session=session,
            event_type="voice.command.failed",
            payload={
                "voice_session_id": session.public_id,
                "command_id": command.public_id,
                "action_type": command.action_type,
                "retryable": command.status == "retryable",
                "attempt_count": command.attempt_count,
            },
        )
        db.commit()
        return

    _finish(
        command,
        command_status="succeeded",
        provider_status=result.provider_status,
        provider_reason=result.provider_reason,
        provider_reference=result.provider_reference,
        provider_result=result.safe_payload,
    )
    _write_event(
        db,
        session=session,
        event_type="voice.command.succeeded",
        payload={
            "voice_session_id": session.public_id,
            "command_id": command.public_id,
            "action_type": command.action_type,
            "provider_status": result.provider_status,
            "provider_reference": result.provider_reference,
        },
    )
    db.commit()


def dispatch_pending_voice_commands(
    db: Session,
    *,
    worker_id: str,
    limit: int = 20,
) -> list[int]:
    command_ids = _claim_due_commands(
        db,
        worker_id=worker_id,
        limit=limit,
    )
    for command_id in command_ids:
        try:
            _dispatch_one(db, command_id=command_id)
        except Exception:
            db.rollback()
            logger.exception(
                "voice_command_dispatch_failed",
                extra={"command_id": command_id, "worker_id": worker_id},
            )
            command = db.get(WebchatVoiceSessionAction, command_id)
            if command is not None and command.status == "dispatching":
                _retry(command, reason="dispatcher_internal_error")
                db.commit()
    return command_ids
