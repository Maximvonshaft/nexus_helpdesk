from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/support_memory_ledger_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, tool_models, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, EventType, MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Customer, Ticket, TicketEvent, TicketOutboundMessage, User  # noqa: E402
from app.services.support_memory_ledger import build_support_memory_ledger  # noqa: E402
from app.tool_models import ToolCallLog  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "support_memory_ledger.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_user(db) -> User:
    row = User(username="ledger_admin", display_name="Ledger Admin", email="ledger@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_ticket(db) -> tuple[Ticket, WebchatConversation, WebchatMessage]:
    customer = Customer(name="Ledger Visitor", external_ref="ledger-visitor")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"LEDGER-{customer.id}",
        title="Support memory ledger",
        description="ledger test",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.human_review_required,
        tracking_number="ABC123456789",
        ai_classification="tracking",
        customer_request="Where is my parcel?",
        required_action="Check latest Speedaf evidence before reply.",
        missing_fields="phone, proof",
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="wc_ledger",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"wc_ledger_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="pytest",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="Ledger Visitor",
        status="open",
        last_intent="tracking",
        last_tracking_number="ABC123456789",
        ai_suspended=True,
        ai_suspended_reason="human_review_requested",
        handoff_status="requested",
    )
    db.add(conversation)
    db.flush()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="Where is my parcel?",
        body_text="Where is my parcel?",
        author_label="Visitor",
        metadata_json=json.dumps({"fact_evidence_present": False, "generated_by": "visitor"}),
    )
    db.add(message)
    db.flush()
    return ticket, conversation, message


def test_support_memory_ledger_derives_safe_state_and_speedaf_evidence(db_session):
    user = make_user(db_session)
    ticket, conversation, message = make_ticket(db_session)
    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=message.id,
        latest_visitor_message_id=message.id,
        status="completed",
        reply_source="ai_runtime",
        bridge_elapsed_ms=321,
        is_public_reply_allowed=True,
    )
    db_session.add(turn)
    db_session.flush()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_status = "completed"
    db_session.add(WebchatEvent(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="ai_turn.completed",
        payload_json=json.dumps({"reply_source": "ai_runtime", "status": "completed"}),
    ))
    db_session.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=user.id,
        event_type=EventType.field_updated,
        field_name="speedaf_work_order",
        new_value="queued",
        note="Speedaf delivery follow-up work order queued.",
        payload_json=json.dumps({"job_id": 88, "workOrderType": "WT0103-05", "waybill_suffix": "6789"}),
    ))
    db_session.add(ToolCallLog(
        tool_name="speedaf.order.query",
        provider="speedaf_mcp",
        tool_type="read_only",
        webchat_conversation_id=conversation.id,
        ticket_id=ticket.id,
        status="success",
        output_summary='{"status":"success","tool_status":"delivered"}',
        redaction_applied=True,
    ))
    db_session.add(TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        body="Operator reply",
        provider_status="webchat_delivered",
        created_by=user.id,
        max_retries=0,
    ))
    db_session.flush()

    ledger = build_support_memory_ledger(db_session, ticket_id=ticket.id, current_user=user)

    assert ledger["source"] == "derived_support_memory_ledger"
    assert ledger["current_intent"] == "tracking"
    assert ledger["missing_fields"] == ["phone", "proof"]
    assert ledger["tracking"]["suffix"] == "456789"
    assert ledger["tracking"]["hash"].startswith("sha256:")
    assert ledger["tracking"]["raw_exposed"] is False
    assert ledger["ai_state"]["ai_suspended"] is True
    assert ledger["latest_speedaf_evidence"] is not None
    assert any(item["kind"] == "tool_call" and item["label"] == "speedaf.order.query" for item in ledger["evidence_timeline"])
    assert any(item["key"] in {"handoff_active", "review_handoff", "required_action"} for item in ledger["next_actions"])
    assert "ABC123456789" not in json.dumps(ledger, ensure_ascii=False)
