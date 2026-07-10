from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_configured_escalation_errors.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, models_osr, operator_models, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.models_osr import RuntimeDecisionAuditRecord  # noqa: E402
from app.services import webchat_ai_safe_service  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatHandoffRequest, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'configured_escalation_errors.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def enable_configured_escalation(monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "webchat_ai_auto_reply_mode", "safe_ai")
    monkeypatch.setattr(
        webchat_ai_safe_service.settings,
        "osr_escalation_orchestration_enabled",
        True,
        raising=False,
    )


def _make_case(db, *, suffix: str, body: str):
    customer = Customer(name=f"Escalation error {suffix}", external_ref=f"escalation-error-{suffix}")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"OSR-ESC-ERR-{customer.id}",
        title="Configured escalation error handling",
        description="Configured escalation error handling",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="webchat",
        country_code="ZZ",
        case_type="delivery_issue",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"configured_escalation_error_{suffix}_{ticket.id}",
        visitor_token_hash=f"token-{suffix}",
        tenant_key="tenant-escalation-error",
        channel_key="webchat",
        ticket_id=ticket.id,
        visitor_name="Escalation Error Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    visitor = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body=body,
        body_text=body,
        message_type="text",
        client_message_id=f"visitor-{suffix}",
    )
    db.add(visitor)
    db.flush()
    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=visitor.id,
        latest_visitor_message_id=visitor.id,
        status="queued",
        is_public_reply_allowed=True,
    )
    db.add(turn)
    db.flush()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_status = "queued"
    conversation.active_ai_for_message_id = visitor.id
    db.commit()
    return ticket, conversation, visitor


def _run(db, *, ticket: Ticket, conversation: WebchatConversation, visitor: WebchatMessage):
    return webchat_ai_safe_service.process_webchat_ai_reply_job(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        visitor_message_id=visitor.id,
    )


def _assert_operator_review(db, *, result, ticket: Ticket, conversation: WebchatConversation, expected_reason: str):
    db.refresh(ticket)
    assert result["status"] == "review_required"
    assert result["reason"] == expected_reason
    assert ticket.conversation_state == ConversationState.human_review_required
    assert db.query(WebchatHandoffRequest).count() == 0
    assert db.query(RuntimeDecisionAuditRecord).count() == 0
    assert (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "agent",
        )
        .count()
        == 0
    )


def test_configured_policy_evaluation_error_routes_to_operator_review(db_session, monkeypatch):
    ticket, conversation, visitor = _make_case(
        db_session,
        suffix="policy-evaluation",
        body="I will initiate a chargeback",
    )
    monkeypatch.setattr(webchat_ai_safe_service, "load_escalation_policies", lambda *_args, **_kwargs: [object()])

    def raise_evaluation_error(*_args, **_kwargs):
        raise RuntimeError("configured policy evaluator unavailable")

    monkeypatch.setattr(webchat_ai_safe_service, "evaluate_escalation", raise_evaluation_error)
    result = _run(db_session, ticket=ticket, conversation=conversation, visitor=visitor)

    _assert_operator_review(
        db_session,
        result=result,
        ticket=ticket,
        conversation=conversation,
        expected_reason="osr_escalation_policy_evaluation_failed",
    )


def test_orchestration_error_routes_to_operator_review(db_session, monkeypatch):
    ticket, conversation, visitor = _make_case(
        db_session,
        suffix="orchestration",
        body="I will initiate a chargeback",
    )
    monkeypatch.setattr(
        webchat_ai_safe_service,
        "_has_configured_escalation_intent",
        lambda *_args, **_kwargs: True,
    )

    def raise_orchestration_error(*_args, **_kwargs):
        raise RuntimeError("escalation orchestration unavailable")

    monkeypatch.setattr(webchat_ai_safe_service, "evaluate_escalation_for_case", raise_orchestration_error)
    result = _run(db_session, ticket=ticket, conversation=conversation, visitor=visitor)

    _assert_operator_review(
        db_session,
        result=result,
        ticket=ticket,
        conversation=conversation,
        expected_reason="osr_escalation_evaluation_failed",
    )
