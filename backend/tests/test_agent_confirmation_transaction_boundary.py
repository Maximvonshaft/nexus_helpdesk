from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.model_registry  # noqa: F401
from app.db import Base
from app.models import Tenant
from app.models_agent_runtime import AgentToolConfirmation
from app.services.agent_confirmation_service import create_or_reuse_confirmation
from app.services.webchat_ai_orchestration_service import (
    _resolve_customer_confirmation,
)
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatEvent, WebchatMessage


def test_confirmation_resolution_commits_before_independent_runtime_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'confirmation-boundary.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    writer = Session()
    reader = Session()
    try:
        tenant = Tenant(
            tenant_key="confirmation-boundary",
            display_name="Confirmation Boundary",
            is_active=True,
        )
        writer.add(tenant)
        writer.flush()
        conversation = WebchatConversation(
            public_id="wc_confirmation_boundary",
            visitor_token_hash="b" * 64,
            tenant_key=tenant.tenant_key,
            channel_key="voice",
            status="open",
            last_seen_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        writer.add(conversation)
        writer.flush()
        confirmation = create_or_reuse_confirmation(
            writer,
            conversation=conversation,
            tool_name="ticket.create",
            arguments={
                "title": "Follow-up",
                "description": "Contact the customer after the call.",
                "priority": "medium",
            },
        )
        response = WebchatMessage(
            conversation_id=conversation.id,
            direction="visitor",
            body="Yes please",
            body_text="Yes please",
            message_type="voice_transcript",
            client_message_id="confirmation-boundary-yes",
            delivery_status="sent",
            created_at=utc_now(),
        )
        writer.add(response)
        writer.commit()

        _resolve_customer_confirmation(
            writer,
            conversation=conversation,
            visitor_message=response,
        )

        observed = reader.get(AgentToolConfirmation, confirmation.id)
        assert observed is not None
        assert observed.status == "confirmed"
        event = (
            reader.query(WebchatEvent)
            .filter(
                WebchatEvent.conversation_id == conversation.id,
                WebchatEvent.event_type == "agent.tool_confirmation.resolved",
            )
            .one()
        )
        assert "confirmed" in (event.payload_json or "")
    finally:
        writer.close()
        reader.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
