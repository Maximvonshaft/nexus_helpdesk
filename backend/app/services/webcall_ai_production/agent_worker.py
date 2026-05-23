from __future__ import annotations

import logging
import os
import time
from uuid import uuid4

from ...db import SessionLocal
from .agent_session_claims import AI_STATUS_JOINING, AI_STATUS_LISTENING, AI_STATUS_SPEAKING, fail_session, mark_status, release_session, claim_next_session
from .audio.livekit_io import LiveKitAgentIO
from .config import get_webcall_ai_production_settings
from .event_service import write_event
from .orchestrator import run_fake_turn, run_session_turn

logger = logging.getLogger(__name__)


def health() -> dict[str, object]:
    settings = get_webcall_ai_production_settings()
    return {
        "ok": True,
        "agent_enabled": settings.agent_enabled,
        "provider_profile": settings.provider_profile,
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "tts_provider": settings.tts_provider,
        "status": settings.status,
        "kill_switch": settings.kill_switch,
        "livekit_configured": settings.livekit_configured,
        "provider_configured": settings.provider_configured,
    }


def _test_fake_heartbeat_enabled() -> bool:
    return (os.getenv("WEBCALL_AI_TEST_FAKE_HEARTBEAT") or "").strip().lower() in {"1", "true", "yes", "on"}


def run_worker_once(worker_id: str) -> dict[str, int | str]:
    settings = get_webcall_ai_production_settings()
    if settings.status != "ready" or not settings.agent_enabled:
        return {"claimed": 0, "processed": 0, "failed": 0, "status": settings.status}
    db = SessionLocal()
    try:
        session = claim_next_session(db, worker_id=worker_id, lease_seconds=settings.agent_lease_seconds)
        if session is None:
            return {"claimed": 0, "processed": 0, "failed": 0, "status": "idle"}
        try:
            mark_status(db, session_id=session.id, worker_id=worker_id, status=AI_STATUS_JOINING)
            io = LiveKitAgentIO(
                room_name=session.provider_room_name,
                participant_identity=f"ai_{session.public_id}"[:160],
                ttl_seconds=settings.max_session_seconds,
            )
            write_event(
                db,
                conversation_id=session.conversation_id,
                ticket_id=session.ticket_id,
                event_type="webcall_ai.agent.joined",
                payload={"voice_session_id": session.public_id, "worker_id": worker_id},
            )
            db.commit()
            mark_status(db, session_id=session.id, worker_id=worker_id, status=AI_STATUS_LISTENING)
            media_turn = io.collect_next_customer_utterance()
            result = run_session_turn(
                db,
                session=session,
                audio=media_turn.customer_audio,
                worker_id=worker_id,
                language=media_turn.language,
            )
            mark_status(db, session_id=session.id, worker_id=worker_id, status=AI_STATUS_SPEAKING)
            tts_payload = result["tts"]
            audio_bytes = tts_payload.get("_audio_bytes") if isinstance(tts_payload, dict) else None
            io.publish_ai_audio(audio_bytes or b"", mime_type=tts_payload["mime_type"])
            release_session(db, session_id=session.id, worker_id=worker_id, reason="turn_complete")
            io.close()
            return {"claimed": 1, "processed": 1, "failed": 0, "status": "processed"}
        except Exception as exc:
            db.rollback()
            fail_session(db, session_id=session.id, worker_id=worker_id, error_code="agent_turn_failed", error_message=type(exc).__name__)
            logger.exception("webcall_ai_agent_turn_failed", extra={"voice_session_id": session.public_id, "worker_id": worker_id})
            return {"claimed": 1, "processed": 0, "failed": 1, "status": "failed"}
    finally:
        db.close()


def main() -> None:
    settings = get_webcall_ai_production_settings()
    worker_id = os.getenv("WEBCALL_AI_AGENT_WORKER_ID") or f"webcall-ai-agent-{uuid4().hex[:12]}"
    logger.info("webcall_ai_agent_worker_starting", extra={"worker_id": worker_id, "agent_enabled": settings.agent_enabled, "provider_profile": settings.provider_profile, "status": settings.status})
    if not settings.agent_enabled:
        logger.info("webcall_ai_agent_worker_disabled")
        return
    interval = max(1, int(os.getenv("WEBCALL_AI_AGENT_POLL_SECONDS", "5")))
    while True:
        if _test_fake_heartbeat_enabled():
            result = run_fake_turn("where is package 123456", language="en")
            logger.info("webcall_ai_agent_worker_test_fake_heartbeat", extra={"fake_turn": result["response"]})
        else:
            result = run_worker_once(worker_id)
            logger.info("webcall_ai_agent_worker_poll", extra=result)
        time.sleep(interval)


if __name__ == "__main__":
    main()
