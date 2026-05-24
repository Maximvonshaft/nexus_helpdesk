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
from .audio.livekit_io import LiveKitAgentIO, VisitorDisconnected
from .config import get_webcall_ai_production_settings
from .event_service import write_event
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
        "kill_switch": settings.kill_switch,
        "livekit_configured": settings.livekit_configured,
        "provider_configured": settings.provider_configured,
        "active_sessions": active_sessions,
        "stale_leases": stale_leases,
        "failed_sessions": failed_sessions,
        "last_heartbeat": last_heartbeat.isoformat() if last_heartbeat else None,
    }


def _test_fake_heartbeat_enabled() -> bool:
    return (os.getenv("WEBCALL_AI_TEST_FAKE_HEARTBEAT") or "").strip().lower() in {"1", "true", "yes", "on"}


def _make_io(session: WebchatVoiceSession, settings) -> LiveKitAgentIO:
    return LiveKitAgentIO(
        room_name=session.provider_room_name,
        participant_identity=f"ai_{session.public_id}"[:160],
        ttl_seconds=settings.max_session_seconds,
        livekit_url=settings.livekit_url,
    )


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
        _speak_greeting(db, session=claimed_session, worker_id=worker_id, io=managed_io)
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
                    max_seconds=float(settings.max_utterance_seconds),
                )
            except VisitorDisconnected:
                release_session(db, session_id=session_id, worker_id=worker_id, reason="visitor_disconnected")
                return {"claimed": 1, "processed": turns, "failed": 0, "status": "visitor_disconnected"}
            mark_status(db, session_id=session_id, worker_id=worker_id, status=AI_STATUS_THINKING)
            db.refresh(claimed_session)
            result = run_session_turn(
                db,
                session=claimed_session,
                audio=media_turn.audio_bytes,
                worker_id=worker_id,
                language=media_turn.language,
                sample_rate=media_turn.sample_rate,
                channels=media_turn.channels,
                mime_type=media_turn.mime_type,
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
            audio_bytes = tts_payload.get("_audio_bytes") if isinstance(tts_payload, dict) else None
            try:
                managed_io.publish_ai_audio(audio_bytes or b"", mime_type=tts_payload["mime_type"])
                write_event(
                    db,
                    conversation_id=claimed_session.conversation_id,
                    ticket_id=claimed_session.ticket_id,
                    event_type="webcall_ai.response.spoken",
                    payload={"voice_session_id": claimed_session.public_id, "turn_id": result.get("turn_id"), "tts_provider": tts_payload.get("provider"), "mime_type": tts_payload["mime_type"]},
                )
                db.commit()
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


def _speak_greeting(db, *, session: WebchatVoiceSession, worker_id: str, io: LiveKitAgentIO) -> None:
    turn = build_handoff_turn(
        db,
        session=session,
        worker_id=worker_id,
        response_text="Hello, this is NexusDesk AI support. Please tell me your tracking number or shipment question.",
        intent="ai_greeting",
        handoff_required=False,
        handoff_reason=None,
    )
    tts_payload = turn["tts"]
    io.publish_ai_audio(tts_payload.get("_audio_bytes") or b"", mime_type=tts_payload["mime_type"])


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
