from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..observability import (
    record_webcall_ai_audio_metric,
    record_webcall_ai_barge_in,
    record_webcall_ai_event_metric,
    record_webcall_ai_stage_metric,
)
from ...utils.time import utc_now
from ...voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession
from ...webchat_models import WebchatEvent


def record_webcall_ai_stage(*, stage: str, status: str = "ok", provider: str | None = None, elapsed_ms: int | float | None = None) -> None:
    record_webcall_ai_stage_metric(stage, status, provider, elapsed_ms)


def record_webcall_ai_event(event_type: str | None) -> None:
    record_webcall_ai_event_metric(event_type)
    if event_type == "webcall_ai.response.interrupted":
        record_webcall_ai_barge_in("barge_in")


def record_webcall_ai_audio(*, provider: str | None, status: str, chunks: int = 0, bytes_count: int = 0) -> None:
    record_webcall_ai_audio_metric(provider, status, chunks=chunks, bytes_count=bytes_count)


def webcall_ai_metrics_snapshot(db: Session, *, window_minutes: int = 60) -> dict[str, Any]:
    safe_window_minutes = max(1, min(int(window_minutes or 60), 24 * 60))
    since = utc_now() - timedelta(minutes=safe_window_minutes)
    event_rows = (
        db.query(WebchatEvent.event_type, func.count(func.distinct(WebchatEvent.id)))
        .join(WebchatVoiceSession, WebchatVoiceSession.conversation_id == WebchatEvent.conversation_id)
        .filter(
            WebchatVoiceSession.mode == "livekit_ai_agent",
            WebchatEvent.created_at >= since,
            WebchatEvent.event_type.like("webcall_ai.%"),
        )
        .group_by(WebchatEvent.event_type)
        .all()
    )
    turn_rows = (
        db.query(WebchatVoiceAITurn.provider, WebchatVoiceAITurn.stt_provider, WebchatVoiceAITurn.tts_provider, func.count(WebchatVoiceAITurn.id), func.avg(WebchatVoiceAITurn.latency_ms))
        .join(WebchatVoiceSession, WebchatVoiceSession.id == WebchatVoiceAITurn.voice_session_id)
        .filter(WebchatVoiceSession.mode == "livekit_ai_agent", WebchatVoiceAITurn.created_at >= since)
        .group_by(WebchatVoiceAITurn.provider, WebchatVoiceAITurn.stt_provider, WebchatVoiceAITurn.tts_provider)
        .all()
    )
    handoff_count = (
        db.query(func.count(WebchatVoiceAIAction.id))
        .join(WebchatVoiceSession, WebchatVoiceSession.id == WebchatVoiceAIAction.voice_session_id)
        .filter(WebchatVoiceSession.mode == "livekit_ai_agent", WebchatVoiceAIAction.created_at >= since, WebchatVoiceAIAction.nexus_decision == "handoff")
        .scalar()
        or 0
    )
    active_sessions = (
        db.query(func.count(WebchatVoiceSession.id))
        .filter(WebchatVoiceSession.mode == "livekit_ai_agent", WebchatVoiceSession.ended_at.is_(None))
        .scalar()
        or 0
    )
    events_by_type = {str(event_type): int(count or 0) for event_type, count in event_rows}
    provider_rows = [
        {
            "llm_provider": provider or "unknown",
            "stt_provider": stt_provider or "unknown",
            "tts_provider": tts_provider or "unknown",
            "turns": int(count or 0),
            "avg_latency_ms": int(avg_latency or 0),
        }
        for provider, stt_provider, tts_provider, count, avg_latency in turn_rows
    ]
    return {
        "window_minutes": safe_window_minutes,
        "active_sessions": int(active_sessions),
        "turn_count": sum(row["turns"] for row in provider_rows),
        "handoff_count": int(handoff_count),
        "barge_in_count": events_by_type.get("webcall_ai.response.interrupted", 0),
        "publish_failed_count": events_by_type.get("webcall_ai.response.publish_failed", 0),
        "spoken_count": events_by_type.get("webcall_ai.response.spoken", 0),
        "events_by_type": events_by_type,
        "provider_rows": provider_rows,
    }


def safe_metric_payload(payload_json: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if key in {"voice_session_id", "turn_id", "reason", "speech_ms", "buffered_frames", "tts_provider", "mime_type"}}
