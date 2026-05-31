from __future__ import annotations

import logging
import os
import signal
import time
from uuid import uuid4

from sqlalchemy import func

from ...db import SessionLocal
from ...utils.time import utc_now
from ...voice_models import WebchatVoiceSession
from .agent_session_claims import (
    AI_STATUS_CLAIMED,
    AI_STATUS_JOINED,
    AI_STATUS_JOINING,
    AI_STATUS_LISTENING,
    AI_STATUS_SPEAKING,
    AI_STATUS_THINKING,
    AI_STATUS_WAITING,
    fail_session,
    mark_status,
    release_session,
    claim_next_session,
    should_continue_session,
)
from .audio.livekit_io import BargeInInterrupted, LiveKitAgentIO, LiveKitIOError, VisitorDisconnected
from .audio.stats import analyze_pcm16_audio
from .config import get_webcall_ai_production_settings
from .event_service import write_event
from .metrics import record_webcall_ai_audio, record_webcall_ai_stage
from .orchestrator import build_handoff_turn, run_fake_turn, run_session_turn

logger = logging.getLogger(__name__)
SHUTDOWN_REQUESTED = False
AI_ACTIVE_STATUSES = {AI_STATUS_WAITING, AI_STATUS_CLAIMED, AI_STATUS_JOINING, AI_STATUS_JOINED, AI_STATUS_LISTENING, AI_STATUS_THINKING, AI_STATUS_SPEAKING}


def _request_shutdown(signum, frame) -> None:
    global SHUTDOWN_REQUESTED
    SHUTDOWN_REQUESTED = True
    logger.info("webcall_ai_agent_shutdown_requested", extra={"signal": signum})


def health() -> dict[str, object]:
    settings = get_webcall_ai_production_settings()
    readiness = _smoke_readiness(settings)
    db = SessionLocal()
    try:
        active_sessions = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.mode == "livekit_ai_agent", WebchatVoiceSession.ai_agent_status.in_(list(AI_ACTIVE_STATUSES))).count()
        failed_sessions = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.mode == "livekit_ai_agent", WebchatVoiceSession.ai_agent_status == "failed").count()
        stale_leases = (
            db.query(WebchatVoiceSession)
            .filter(
                WebchatVoiceSession.mode == "livekit_ai_agent",
                WebchatVoiceSession.ai_agent_lease_expires_at.is_not(None),
                WebchatVoiceSession.ai_agent_lease_expires_at < utc_now(),
            )
            .count()
        )
        last_heartbeat = db.query(func.max(WebchatVoiceSession.ai_agent_last_heartbeat_at)).filter(WebchatVoiceSession.mode == "livekit_ai_agent").scalar()
    finally:
        db.close()
    return {
        "ok": settings.status == "ready",
        "agent_enabled": settings.agent_enabled,
        "provider_profile": settings.provider_profile,
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "tts_provider": settings.tts_provider,
        "status": settings.status,
        "smoke_status": readiness["final_status"],
        "readiness": readiness,
        "kill_switch": settings.kill_switch,
        "rollout_mode": settings.public_rollout_mode,
        "livekit_configured": settings.livekit_configured,
        "stt_configured": readiness["stt_configured"],
        "llm_configured": readiness["llm_configured"],
        "tts_configured": readiness["tts_configured"],
        "provider_configured": settings.provider_configured,
        "tracking_bridge_configured": readiness["tracking_bridge_configured"],
        "fake_heartbeat_enabled": readiness["fake_heartbeat_enabled"],
        "recording_enabled": readiness["recording_enabled"],
        "raw_audio_persistence": readiness["raw_audio_persistence"],
        "dangerous_write_actions_enabled": readiness["dangerous_write_actions_enabled"],
        "active_sessions": active_sessions,
        "stale_leases": stale_leases,
        "failed_sessions": failed_sessions,
        "last_heartbeat": last_heartbeat.isoformat() if last_heartbeat else None,
    }


def _smoke_readiness(settings) -> dict[str, object]:
    stt_configured = settings.stt_configured
    llm_configured = settings.llm_configured
    tts_configured = settings.tts_configured
    tracking_bridge_configured = bool((os.getenv("TRACKING_LOOKUP_ENDPOINT") or "").strip() and (os.getenv("TRACKING_LOOKUP_API_KEY_FILE") or "").strip())
    fake_heartbeat_enabled = _test_fake_heartbeat_enabled()
    recording_enabled = (os.getenv("WEBCHAT_VOICE_RECORDING_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    dangerous_write_actions_enabled = bool(settings.allow_speedaf_work_order or settings.allow_cancel or settings.allow_address_update)
    raw_audio_persistence = bool(settings.record_raw_audio)
    blockers = []
    degraded = []
    if not settings.production_enabled:
        blockers.append("production_disabled")
    if not settings.agent_enabled:
        blockers.append("agent_disabled")
    if settings.kill_switch:
        blockers.append("kill_switch")
    if settings.public_rollout_mode == "off":
        blockers.append("rollout_off")
    if not settings.livekit_configured:
        blockers.append("livekit_not_configured")
    if fake_heartbeat_enabled:
        blockers.append("fake_heartbeat_enabled")
    if recording_enabled or raw_audio_persistence:
        blockers.append("raw_audio_or_recording_enabled")
    if dangerous_write_actions_enabled:
        blockers.append("dangerous_write_actions_enabled")
    if not stt_configured:
        blockers.append("stt_not_configured")
    if not llm_configured:
        blockers.append("llm_not_configured")
    if not tts_configured:
        blockers.append("tts_not_configured")
    if not tracking_bridge_configured:
        degraded.append("tracking_bridge_not_configured")
    final_status = "blocked" if blockers else ("degraded" if degraded else "ready_for_internal_smoke")
    return {
        "livekit_configured": settings.livekit_configured,
        "stt_configured": stt_configured,
        "llm_configured": llm_configured,
        "tts_configured": tts_configured,
        "tracking_bridge_configured": tracking_bridge_configured,
        "kill_switch": settings.kill_switch,
        "rollout_mode": settings.public_rollout_mode,
        "fake_heartbeat_enabled": fake_heartbeat_enabled,
        "recording_enabled": recording_enabled,
        "raw_audio_persistence": raw_audio_persistence,
        "dangerous_write_actions_enabled": dangerous_write_actions_enabled,
        "blockers": blockers,
        "degraded": degraded,
        "final_status": final_status,
    }


def _test_fake_heartbeat_enabled() -> bool:
    return (os.getenv("WEBCALL_AI_TEST_FAKE_HEARTBEAT") or "").strip().lower() in {"1", "true", "yes", "on"}


def _make_io(session: WebchatVoiceSession, settings) -> LiveKitAgentIO:
    return LiveKitAgentIO(
        room_name=session.provider_room_name,
        participant_identity=f"ai_{session.public_id}"[:160],
        ttl_seconds=settings.max_session_seconds,
        livekit_url=settings.livekit_url,
        telemetry_callback=_livekit_telemetry_writer(session),
    )


def _livekit_telemetry_writer(session: WebchatVoiceSession):
    def record(event_type: str, payload: dict) -> None:
        db = SessionLocal()
        try:
            enriched = {"voice_session_id": session.public_id, **_safe_audio_event_payload(payload)}
            write_event(
                db,
                conversation_id=session.conversation_id,
                ticket_id=session.ticket_id,
                event_type=event_type,
                payload=enriched,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("webcall_ai_livekit_event_write_failed", extra={"voice_session_id": session.public_id, "event_type": event_type})
        finally:
            db.close()

    return record


def run_worker_once(worker_id: str) -> dict[str, int | str]:
    settings = get_webcall_ai_production_settings()
    if settings.status != "ready" or not settings.agent_enabled:
        return {"claimed": 0, "processed": 0, "failed": 0, "status": settings.status}
    db = SessionLocal()
    try:
        session = claim_next_session(db, worker_id=worker_id, lease_seconds=settings.agent_lease_seconds)
        if session is None:
            return {"claimed": 0, "processed": 0, "failed": 0, "status": "idle"}
        return run_claimed_session_loop(session.id, worker_id=worker_id)
    finally:
        db.close()


def run_claimed_session_loop(session_id: int, *, worker_id: str, io: LiveKitAgentIO | None = None) -> dict[str, int | str]:
    settings = get_webcall_ai_production_settings()
    db = SessionLocal()
    claimed_session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.id == session_id).one()
    managed_io = io or _make_io(claimed_session, settings)
    turns = 0
    try:
        mark_status(db, session_id=session_id, worker_id=worker_id, status=AI_STATUS_JOINING)
        managed_io.connect()
        mark_status(db, session_id=session_id, worker_id=worker_id, status=AI_STATUS_JOINED)
        write_event(
            db,
            conversation_id=claimed_session.conversation_id,
            ticket_id=claimed_session.ticket_id,
            event_type="webcall_ai.agent.joined",
            payload={"voice_session_id": claimed_session.public_id, "worker_id": worker_id},
        )
        db.commit()
        if _speak_greeting(db, session=claimed_session, worker_id=worker_id, io=managed_io):
            _sleep_post_tts_listen_grace(settings)
        started_at = time.monotonic()
        while not SHUTDOWN_REQUESTED and (time.monotonic() - started_at) < settings.max_session_seconds:
            can_continue, reason = should_continue_session(db, session_id=session_id, worker_id=worker_id)
            if not can_continue:
                release_session(db, session_id=session_id, worker_id=worker_id, reason=reason)
                return {"claimed": 1, "processed": turns, "failed": 0, "status": reason}
            mark_status(db, session_id=session_id, worker_id=worker_id, status=AI_STATUS_LISTENING)
            write_event(
                db,
                conversation_id=claimed_session.conversation_id,
                ticket_id=claimed_session.ticket_id,
                event_type="webcall_ai.agent.listening",
                payload={"voice_session_id": claimed_session.public_id},
            )
            db.commit()
            try:
                media_turn = managed_io.collect_next_customer_utterance(
                    timeout_seconds=float(os.getenv("WEBCALL_AI_UTTERANCE_TIMEOUT_SECONDS", "20")),
                    max_seconds=float(settings.max_utterance_audio_ms) / 1000.0,
                )
            except VisitorDisconnected:
                release_session(db, session_id=session_id, worker_id=worker_id, reason="visitor_disconnected")
                return {"claimed": 1, "processed": turns, "failed": 0, "status": "visitor_disconnected"}
            except LiveKitIOError:
                _write_audio_ingress_empty_event(db, session=claimed_session, io=managed_io)
                raise
            mark_status(db, session_id=session_id, worker_id=worker_id, status=AI_STATUS_THINKING)
            db.refresh(claimed_session)
            audio_stats = _audio_stats_payload(claimed_session, media_turn)
            write_event(
                db,
                conversation_id=claimed_session.conversation_id,
                ticket_id=claimed_session.ticket_id,
                event_type="webcall_ai.livekit.audio_frame_stats",
                payload=audio_stats,
            )
            db.flush()
            result = run_session_turn(
                db,
                session=claimed_session,
                audio=media_turn.audio_bytes,
                worker_id=worker_id,
                language=media_turn.language,
                sample_rate=media_turn.sample_rate,
                channels=media_turn.channels,
                mime_type=media_turn.mime_type,
                audio_stats=audio_stats,
            )
            turns += 1
            mark_status(db, session_id=session_id, worker_id=worker_id, status=AI_STATUS_SPEAKING)
            write_event(
                db,
                conversation_id=claimed_session.conversation_id,
                ticket_id=claimed_session.ticket_id,
                event_type="webcall_ai.agent.speaking",
                payload={"voice_session_id": claimed_session.public_id},
            )
            db.commit()
            tts_payload = result["tts"]
            try:
                _publish_tts_payload(managed_io, tts_payload)
                write_event(
                    db,
                    conversation_id=claimed_session.conversation_id,
                    ticket_id=claimed_session.ticket_id,
                    event_type="webcall_ai.response.spoken",
                    payload={"voice_session_id": claimed_session.public_id, "turn_id": result.get("turn_id"), "tts_provider": tts_payload.get("provider"), "mime_type": tts_payload["mime_type"]},
                )
                db.commit()
            except BargeInInterrupted as exc:
                db.rollback()
                if hasattr(managed_io, "cancel_ai_audio_stream"):
                    managed_io.cancel_ai_audio_stream(reason="barge_in")
                write_event(
                    db,
                    conversation_id=claimed_session.conversation_id,
                    ticket_id=claimed_session.ticket_id,
                    event_type="webcall_ai.response.interrupted",
                    payload={
                        "voice_session_id": claimed_session.public_id,
                        "turn_id": result.get("turn_id"),
                        "reason": "barge_in",
                        "speech_ms": exc.speech_ms,
                        "buffered_frames": exc.buffered_frames,
                    },
                )
                db.commit()
                continue
            except Exception:
                db.rollback()
                write_event(
                    db,
                    conversation_id=claimed_session.conversation_id,
                    ticket_id=claimed_session.ticket_id,
                    event_type="webcall_ai.response.publish_failed",
                    payload={"voice_session_id": claimed_session.public_id, "turn_id": result.get("turn_id")},
                )
                db.commit()
                raise
            if bool(result.get("handoff_required")):
                release_session(db, session_id=session_id, worker_id=worker_id, reason=str(result.get("handoff_reason") or "handoff_required"))
                return {"claimed": 1, "processed": turns, "failed": 0, "status": "handoff_required"}
            _sleep_post_tts_listen_grace(settings)
        release_session(db, session_id=session_id, worker_id=worker_id, reason="max_session_seconds")
        return {"claimed": 1, "processed": turns, "failed": 0, "status": "max_session_seconds"}
    except Exception as exc:
        db.rollback()
        fail_session(db, session_id=session_id, worker_id=worker_id, error_code="agent_loop_failed", error_message=type(exc).__name__)
        try:
            write_event(
                db,
                conversation_id=claimed_session.conversation_id,
                ticket_id=claimed_session.ticket_id,
                event_type="webcall_ai.agent.failed",
                payload={"voice_session_id": claimed_session.public_id, "error_code": "agent_loop_failed"},
            )
            db.commit()
        except Exception:
            db.rollback()
        logger.exception("webcall_ai_agent_loop_failed", extra={"voice_session_id": claimed_session.public_id, "worker_id": worker_id, "error": type(exc).__name__})
        return {"claimed": 1, "processed": turns, "failed": 1, "status": "failed"}
    finally:
        managed_io.close()
        db.close()


def _speak_greeting(db, *, session: WebchatVoiceSession, worker_id: str, io: LiveKitAgentIO) -> bool:
    turn = build_handoff_turn(
        db,
        session=session,
        worker_id=worker_id,
        response_text="Hello, this is AI support. Please tell me your tracking number or shipment question.",
        intent="ai_greeting",
        handoff_required=False,
        handoff_reason=None,
    )
    tts_payload = turn["tts"]
    try:
        _publish_tts_payload(io, tts_payload)
        return True
    except BargeInInterrupted as exc:
        if hasattr(io, "cancel_ai_audio_stream"):
            io.cancel_ai_audio_stream(reason="barge_in")
        write_event(
            db,
            conversation_id=session.conversation_id,
            ticket_id=session.ticket_id,
            event_type="webcall_ai.response.interrupted",
            payload={
                "voice_session_id": session.public_id,
                "turn_id": turn.get("turn_id"),
                "reason": "barge_in",
                "speech_ms": exc.speech_ms,
                "buffered_frames": exc.buffered_frames,
            },
        )
        db.commit()
        return False


def _publish_tts_payload(io: LiveKitAgentIO, tts_payload: dict) -> None:
    provider = str(tts_payload.get("provider") or "unknown") if isinstance(tts_payload, dict) else "unknown"
    started = time.monotonic()
    chunks = _tts_audio_chunks(tts_payload)
    stats = {"chunks": 0, "bytes": 0}
    try:
        if chunks and hasattr(io, "publish_ai_audio_stream"):
            io.publish_ai_audio_stream(_meter_tts_chunks(chunks, provider=provider, stats=stats, started=started, tts_payload=tts_payload), mime_type=tts_payload["mime_type"])
            record_webcall_ai_audio(provider=provider, status="ok", chunks=stats["chunks"], bytes_count=stats["bytes"])
            record_webcall_ai_stage(stage="audio_publish", status="ok", provider=provider, elapsed_ms=int((time.monotonic() - started) * 1000))
            return
        audio_bytes = tts_payload.get("_audio_bytes") if isinstance(tts_payload, dict) else None
        io.publish_ai_audio(audio_bytes or b"", mime_type=tts_payload["mime_type"])
        byte_count = len(audio_bytes or b"")
        record_webcall_ai_audio(provider=provider, status="ok", chunks=1 if audio_bytes else 0, bytes_count=byte_count)
        record_webcall_ai_stage(stage="audio_publish", status="ok", provider=provider, elapsed_ms=int((time.monotonic() - started) * 1000))
    except BargeInInterrupted:
        _cancel_tts_payload(tts_payload, reason="barge_in")
        record_webcall_ai_audio(provider=provider, status="interrupted", chunks=stats["chunks"], bytes_count=stats["bytes"])
        record_webcall_ai_stage(stage="audio_publish", status="interrupted", provider=provider, elapsed_ms=int((time.monotonic() - started) * 1000))
        raise
    except Exception:
        record_webcall_ai_audio(provider=provider, status="failed", chunks=stats["chunks"], bytes_count=stats["bytes"])
        record_webcall_ai_stage(stage="audio_publish", status="failed", provider=provider, elapsed_ms=int((time.monotonic() - started) * 1000))
        raise


def _tts_audio_chunks(tts_payload: dict):
    chunks = tts_payload.get("_audio_stream") if isinstance(tts_payload, dict) else None
    if chunks:
        return chunks
    chunks = tts_payload.get("_audio_chunks") if isinstance(tts_payload, dict) else None
    if chunks:
        return chunks
    return None


def _meter_tts_chunks(chunks, *, provider: str, stats: dict[str, int], started: float, tts_payload: dict):
    first_chunk_recorded = False
    try:
        for chunk in chunks:
            audio = bytes(getattr(chunk, "audio_bytes", b"") or b"")
            if not audio:
                try:
                    yield chunk
                except GeneratorExit:
                    _cancel_tts_payload(tts_payload, reason="barge_in")
                    raise
                continue
            if not first_chunk_recorded:
                first_chunk_recorded = True
                provider_latency = getattr(chunk, "provider_latency_ms", None)
                latency_ms = int(provider_latency) if isinstance(provider_latency, int | float) else int((time.monotonic() - started) * 1000)
                record_webcall_ai_stage(stage="tts_first_audio", provider=provider, elapsed_ms=latency_ms)
                turn_started_at = tts_payload.get("_turn_started_at") if isinstance(tts_payload, dict) else None
                if isinstance(turn_started_at, int | float):
                    record_webcall_ai_stage(stage="end_to_first_audio", provider=provider, elapsed_ms=int((time.monotonic() - float(turn_started_at)) * 1000))
            stats["chunks"] += 1
            stats["bytes"] += len(audio)
            try:
                yield chunk
            except GeneratorExit:
                _cancel_tts_payload(tts_payload, reason="barge_in")
                raise
        tts_started_at = tts_payload.get("_tts_started_at") if isinstance(tts_payload, dict) else None
        if isinstance(tts_started_at, int | float):
            record_webcall_ai_stage(stage="tts_total", provider=provider, elapsed_ms=int((time.monotonic() - float(tts_started_at)) * 1000))
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            close()


def _cancel_tts_payload(tts_payload: dict, *, reason: str) -> None:
    token = tts_payload.get("_cancel_token") if isinstance(tts_payload, dict) else None
    cancel = getattr(token, "cancel", None)
    if callable(cancel):
        cancel(reason)


def _audio_stats_payload(session: WebchatVoiceSession, media_turn) -> dict[str, object]:
    provided = getattr(media_turn, "audio_stats", None)
    if isinstance(provided, dict):
        stats = dict(provided)
    else:
        stats = analyze_pcm16_audio(
            media_turn.audio_bytes,
            sample_rate=media_turn.sample_rate,
            channels=media_turn.channels,
        ).as_payload()
    stats.update(
        {
            "voice_session_id": session.public_id,
            "turn_index": int(session.ai_turn_count or 0) + 1,
            "participant_identity": stats.get("participant_identity"),
            "track_sid": stats.get("track_sid"),
        }
    )
    return _safe_audio_event_payload(stats)


def _safe_audio_event_payload(payload: dict | None) -> dict[str, object]:
    allowed = {
        "voice_session_id",
        "turn_index",
        "participant_identity",
        "track_sid",
        "track_kind",
        "track_muted",
        "remote_track_seen",
        "audio_track_muted",
        "frame_count",
        "audio_ms",
        "pcm_bytes",
        "sample_rate",
        "channels",
        "rms_min",
        "rms_avg",
        "rms_max",
        "audio_input_classification",
        "capture_mode",
        "capture_min_audio_ms",
        "capture_max_audio_ms",
        "capture_silence_end_ms",
        "capture_end_reason",
    }
    sanitized: dict[str, object] = {}
    for key, value in (payload or {}).items():
        if key not in allowed:
            continue
        if isinstance(value, str):
            sanitized[key] = value[:240]
        elif isinstance(value, bool) or isinstance(value, int) or value is None:
            sanitized[key] = value
    return sanitized


def _write_audio_ingress_empty_event(db, *, session: WebchatVoiceSession, io: LiveKitAgentIO) -> None:
    snapshot = io.audio_ingress_snapshot() if hasattr(io, "audio_ingress_snapshot") else None
    if not isinstance(snapshot, dict):
        snapshot = analyze_pcm16_audio(b"", sample_rate=48000, channels=1, frame_count=0, remote_track_seen=False).as_payload()
    snapshot.update(
        {
            "voice_session_id": session.public_id,
            "turn_index": int(session.ai_turn_count or 0) + 1,
            "empty_reason": snapshot.get("audio_input_classification") or "no_pcm_frames",
        }
    )
    payload = _safe_audio_event_payload(snapshot)
    payload["empty_reason"] = str(snapshot.get("empty_reason") or "no_pcm_frames")[:240]
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.stt.empty_with_audio_stats",
        payload=payload,
    )
    db.commit()


def _sleep_post_tts_listen_grace(settings) -> None:
    grace_ms = int(getattr(settings, "post_tts_listen_grace_ms", 800) or 0)
    if grace_ms > 0:
        time.sleep(grace_ms / 1000.0)


def main() -> None:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    settings = get_webcall_ai_production_settings()
    worker_id = os.getenv("WEBCALL_AI_AGENT_WORKER_ID") or f"webcall-ai-agent-{uuid4().hex[:12]}"
    logger.info("webcall_ai_agent_worker_starting", extra={"worker_id": worker_id, "agent_enabled": settings.agent_enabled, "provider_profile": settings.provider_profile, "status": settings.status})
    if not settings.agent_enabled:
        logger.info("webcall_ai_agent_worker_disabled")
        return
    interval = max(1, int(os.getenv("WEBCALL_AI_AGENT_POLL_SECONDS", "5")))
    while not SHUTDOWN_REQUESTED:
        if _test_fake_heartbeat_enabled():
            result = run_fake_turn("where is package 123456", language="en")
            logger.info("webcall_ai_agent_worker_test_fake_heartbeat", extra={"fake_turn": result["response"]})
        else:
            result = run_worker_once(worker_id)
            logger.info("webcall_ai_agent_worker_poll", extra=result)
        time.sleep(interval)


if __name__ == "__main__":
    main()
