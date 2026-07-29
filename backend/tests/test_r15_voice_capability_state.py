from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/r15-voice-capability.db")

from app.db import Base
from app.model_registry import register_all_models
from app.services.livekit_agent_turn_service import _record_segment
from app.services.voice_compliance_service import (
    POLICY_VERSION,
    apply_session_compliance_state,
    policy_prompt,
    record_evidence,
)
from app.voice_models import WebchatVoiceSession, WebchatVoiceSessionAction, WebchatVoiceTranscriptSegment
from app.webchat_models import WebchatConversation

register_all_models()


def _session(db, suffix: str) -> WebchatVoiceSession:  # noqa: ANN001
    conversation = WebchatConversation(
        public_id=f"r15-voice-conversation-{suffix}",
        visitor_token_hash=f"hash-{suffix}",
        tenant_key="default",
        channel_key="voice",
        status="open",
    )
    db.add(conversation)
    db.flush()
    session = WebchatVoiceSession(
        public_id=f"r15-voice-session-{suffix}",
        conversation_id=conversation.id,
        provider="livekit",
        provider_room_name=f"room-{suffix}",
        status="active",
        mode="browser_ai",
        direction="inbound",
        recording_status="disabled",
        transcript_status="disabled",
    )
    db.add(session)
    db.flush()
    return session


def test_authorization_command_and_provider_receipt_are_distinct_states(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'voice.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        session = _session(db, "recording")
        prompt = policy_prompt("recording", "explicit_consent")
        record_evidence(
            db,
            session=session,
            capability="recording",
            policy="explicit_consent",
            policy_version=POLICY_VERSION,
            prompt_sha256=prompt["prompt_sha256"],
            source="browser",
            decision="accepted",
            participant_identity="customer",
            idempotency_key="r15-recording-consent",
        )
        apply_session_compliance_state(
            db,
            session=session,
            recording_policy="explicit_consent",
            transcription_policy="disabled",
        )
        assert session.recording_status == "authorized"
        assert session.transcript_status == "disabled"

        command = WebchatVoiceSessionAction(
            public_id="r15-recording-command",
            voice_session_id=session.id,
            conversation_id=session.conversation_id,
            action_type="recording_start",
            idempotency_key="r15-recording-start",
            status="requested",
            provider_status="pending",
        )
        db.add(command)
        db.flush()
        db.expire(session)
        db.refresh(session)
        assert session.recording_status == "start_requested"

        command.status = "succeeded"
        command.provider_status = "completed"
        db.flush()
        db.expire(session)
        db.refresh(session)
        assert session.recording_status == "active"

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_transcript_becomes_active_only_when_real_segment_is_persisted(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'transcript.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        session = _session(db, "transcript")
        prompt = policy_prompt("transcript_persistence", "notice")
        record_evidence(
            db,
            session=session,
            capability="transcript_persistence",
            policy="notice",
            policy_version=POLICY_VERSION,
            prompt_sha256=prompt["prompt_sha256"],
            source="sip_controller",
            decision="notice_delivered",
            participant_identity="caller",
            idempotency_key="r15-transcript-notice",
        )
        apply_session_compliance_state(
            db,
            session=session,
            recording_policy="disabled",
            transcription_policy="notice",
        )
        assert session.transcript_status == "authorized"
        assert db.query(WebchatVoiceTranscriptSegment).count() == 0

        _record_segment(
            db,
            session=session,
            segment_id="provider-segment-1",
            speaker_type="visitor",
            participant_identity="caller",
            text="The actual provider transcript segment.",
            language="en",
        )
        db.flush()
        assert session.transcript_status == "active"
        assert db.query(WebchatVoiceTranscriptSegment).count() == 1

        unauthorized = _session(db, "unauthorized")
        unauthorized.transcript_status = "consent_required"
        _record_segment(
            db,
            session=unauthorized,
            segment_id="provider-segment-blocked",
            speaker_type="visitor",
            participant_identity="caller-2",
            text="Must not be persisted.",
            language="en",
        )
        db.flush()
        assert unauthorized.transcript_status == "consent_required"
        assert (
            db.query(WebchatVoiceTranscriptSegment)
            .filter(WebchatVoiceTranscriptSegment.voice_session_id == unauthorized.id)
            .count()
            == 0
        )

    Base.metadata.drop_all(engine)
    engine.dispose()
