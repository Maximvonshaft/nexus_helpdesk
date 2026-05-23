from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from ...enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from ...models import Ticket
from ...utils.time import utc_now
from ...voice_models import WebchatVoiceSession
from ...webchat_models import WebchatConversation
from .config import WebCallAISettings
from .lifecycle import is_webcall_ai_session_claimable


def resolve_or_create_pilot_voice_session(
    db: Session,
    *,
    settings: WebCallAISettings,
    mode: str,
) -> WebchatVoiceSession | None:
    if settings.pilot_session_public_id:
        session = (
            db.query(WebchatVoiceSession)
            .filter(WebchatVoiceSession.public_id == settings.pilot_session_public_id)
            .first()
        )
        if session is None or not is_webcall_ai_session_claimable(db, session.id):
            return None
        return session

    if not settings.pilot_fixture_enabled or not settings.pilot_fixture_allow_db_write:
        return None
    if settings.app_env == "production":
        return None
    return _create_fixture_voice_session(db, mode=mode)


def _create_fixture_voice_session(db: Session, *, mode: str) -> WebchatVoiceSession:
    now = utc_now()
    suffix = uuid4().hex[:12]
    ticket = Ticket(
        ticket_no=f"PILOT-{suffix}",
        title="WebCall AI pilot fixture",
        description="Synthetic WebCall AI pilot fixture",
        source=TicketSource.ai_intake,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        conversation_state=ConversationState.ai_active,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"pilot_conv_{suffix}",
        visitor_token_hash=f"pilot_hash_{suffix}",
        tenant_key="pilot",
        channel_key="webcall_ai",
        ticket_id=ticket.id,
        visitor_ref=f"pilot_fixture_{mode}",
        status="open",
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    db.flush()
    session = WebchatVoiceSession(
        public_id=f"pilot_fixture_{suffix}",
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        provider="livekit",
        provider_room_name=f"pilot_room_{suffix}",
        status="ringing",
        mode="visitor_to_agent",
        transcript_status="enabled",
        recording_status="disabled",
        ai_language="en",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session
