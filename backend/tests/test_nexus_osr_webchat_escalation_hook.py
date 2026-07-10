from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_webchat_escalation_hook_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, models_osr, operator_models, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.models_osr import EscalationPolicyRecord, HumanHoursPolicyRecord, RuntimeDecisionAuditRecord  # noqa: E402
from app.services import webchat_ai_safe_service  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatHandoffRequest, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_webchat_escalation_hook.db"
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


@pytest.fixture(autouse=True)
def reset_webchat_settings(monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "webchat_ai_auto_reply_mode", "safe_ai")
    monkeypatch.setattr(webchat_ai_safe_service.settings, "osr_escalation_orchestration_enabled", False, raising=False)


def _online_hours() -> dict[str, list[list[str]]]:
    return {day: [["00:00", "23:59"]] for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def add_hours(db, *, online: bool, country_code: str = "ZZ", channel: str = "webchat", queue_key: str = "zz-webchat") -> None:
    db.add(HumanHoursPolicyRecord(
        country_code=country_code,
        channel=channel,
        queue_key=queue_key,
        timezone_name="UTC",
        working_hours_json=_online_hours() if online else {},
        holiday_calendar_json=[],
        auto_ticket_when_offline=True,
        handoff_enabled=True,
        enabled=True,
    ))


def add_escalation(db, risk_key: str, pattern: str, *, max_ai_attempts: int = 0) -> None:
    db.add(EscalationPolicyRecord(
        risk_key=risk_key,
        country_code="ZZ",
        channel="webchat",
        trigger_patterns_json=[pattern],
        max_ai_attempts=max_ai_attempts,
        action="handoff_or_ticket",
        forbidden_commitments_json=["do_not_confirm_resolution"],
        enabled=True,
    ))


def make_webchat_case(db, *, body: str, suffix: str = "case"):
    customer = Customer(name=f"OSR Hook Visitor {suffix}", external_ref=f"osr-hook-{suffix}")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"OSR-HOOK-{customer.id}",
        title="OSR WebChat hook",
        description="OSR WebChat hook",
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
        public_id=f"osr_hook_{suffix}_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="tenant-a",
        channel_key="webchat",
        ticket_id=ticket.id,
        visitor_name="OSR Hook Visitor",
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
    db.flush()
    return ticket, conversation, visitor, turn


def run_hook(db, *, ticket: Ticket, conversation: WebchatConversation, visitor: WebchatMessage):
    return webchat_ai_safe_service.process_webchat_ai_reply_job(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        visitor_message_id=visitor.id,
    )


def test_flag_off_preserves_existing_high_risk_review_path(db_session):
    ticket, conversation, visitor, _turn = make_webchat_case(db_session, body="I want compensation", suffix="flag-off")
    db_session.commit()

    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)

    assert result["status"] == "review_required"
    assert result["reason"] == "webchat_safe_ai_high_risk_review"
    assert db_session.query(WebchatHandoffRequest).count() == 0


def test_flag_off_configured_only_pattern_preserves_legacy_runtime(db_session, monkeypatch):
    add_hours(db_session, online=True)
    add_escalation(db_session, "payment_dispute", r"\bchargeback\b", max_ai_attempts=0)
    ticket, conversation, visitor, _turn = make_webchat_case(db_session, body="I will start a chargeback", suffix="configured-flag-off")
    db_session.commit()

    def fake_legacy(*_args, **_kwargs):
        return {"status": "done", "message_id": None, "reply_source": "legacy_flag_off"}

    def fail_policy_read(*_args, **_kwargs):
        raise AssertionError("configured escalation policies must not be read while the feature flag is off")

    monkeypatch.setattr(webchat_ai_safe_service, "load_escalation_policies", fail_policy_read)
    monkeypatch.setattr(webchat_ai_safe_service, "_legacy_process_webchat_ai_reply_job", fake_legacy)
    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)

    assert webchat_ai_safe_service._has_high_risk_intent(visitor.body) is False
    assert result["status"] == "done"
    assert result["reply_source"] == "legacy_flag_off"
    assert db_session.query(WebchatHandoffRequest).count() == 0
    assert db_session.query(RuntimeDecisionAuditRecord).count() == 0


def test_flag_on_online_escalation_uses_existing_handoff_service_after_webchat_osr_audit(db_session, monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "osr_escalation_orchestration_enabled", True, raising=False)
    add_hours(db_session, online=True)
    add_escalation(db_session, "legal_threat", "legal", max_ai_attempts=0)
    ticket, conversation, visitor, turn = make_webchat_case(db_session, body="This is a legal issue", suffix="online-handoff")
    db_session.commit()

    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)
    handoff = db_session.query(WebchatHandoffRequest).first()
    db_session.refresh(turn)
    db_session.refresh(conversation)

    assert result["reason"] == "osr_handoff_requested"
    assert result["runtime_handoff_required"] is True
    assert result["osr_escalation"]["webchat_runtime_audit_id"] is not None
    assert handoff is not None
    assert handoff.source == "nexus_osr"
    assert conversation.ai_suspended is True
    assert turn.status == "cancelled"
    assert db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "agent").count() == 0


def test_flag_on_configured_chargeback_pattern_reaches_policy_without_legacy_term(db_session, monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "osr_escalation_orchestration_enabled", True, raising=False)
    add_hours(db_session, online=True)
    add_escalation(db_session, "payment_dispute", r"\bchargeback\b", max_ai_attempts=0)
    raw_tracking = "CH020000123456"
    raw_email = "customer@example.com"
    raw_phone = "+382 68123456"
    body = f"I will start a chargeback for {raw_tracking}; email {raw_email}; phone {raw_phone}"
    ticket, conversation, visitor, turn = make_webchat_case(db_session, body=body, suffix="configured-chargeback")
    db_session.commit()

    assert webchat_ai_safe_service._has_high_risk_intent(body) is False
    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)
    handoff = db_session.query(WebchatHandoffRequest).one()
    db_session.refresh(turn)

    assert result["reason"] == "osr_handoff_requested"
    assert result["runtime_handoff_required"] is True
    assert result["osr_escalation"]["risk_key"] == "payment_dispute"
    assert handoff.source == "nexus_osr"
    assert turn.status == "cancelled"
    assert db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "agent").count() == 0

    rows = db_session.query(RuntimeDecisionAuditRecord).all()
    serialized = json.dumps([
        {"decision": row.decision_json, "case_context": row.case_context_json, "violations": row.violations_json, "warnings": row.warnings_json}
        for row in rows
    ], ensure_ascii=False, default=str)
    assert raw_tracking not in serialized
    assert raw_email not in serialized
    assert raw_phone not in serialized
    assert "raw_prompt" not in serialized
    assert "raw_provider_payload" not in serialized
    assert "raw_tool_payload" not in serialized


def test_flag_on_lawyer_wording_reaches_osr_legal_threat_policy(db_session, monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "osr_escalation_orchestration_enabled", True, raising=False)
    add_hours(db_session, online=False)
    add_escalation(db_session, "legal_threat", "lawyer", max_ai_attempts=0)
    ticket, conversation, visitor, turn = make_webchat_case(db_session, body="I will contact my lawyer", suffix="lawyer-threat")
    db_session.commit()

    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)
    db_session.refresh(turn)

    assert result["reason"] == "osr_ticket_created"
    assert result["osr_escalation"]["risk_key"] == "legal_threat"
    assert result["osr_escalation"]["webchat_runtime_audit_id"] is not None
    assert turn.status == "failed"
    assert db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "agent").count() == 0


def test_flag_on_offline_escalation_creates_or_reuses_ticket_without_customer_body_after_webchat_osr_audit(db_session, monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "osr_escalation_orchestration_enabled", True, raising=False)
    add_hours(db_session, online=False)
    add_escalation(db_session, "compensation", "compensation", max_ai_attempts=0)
    ticket, conversation, visitor, turn = make_webchat_case(db_session, body="I want compensation", suffix="offline-ticket")
    db_session.commit()

    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)
    db_session.refresh(turn)

    assert result["reason"] == "osr_ticket_created"
    assert result["osr_escalation"]["ticket_id"] == ticket.id
    assert result["osr_escalation"]["ticket_created"] is False
    assert result["osr_escalation"]["webchat_runtime_audit_id"] is not None
    assert turn.status == "failed"
    assert db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "agent").count() == 0


def test_flag_on_compensation_before_max_attempts_continues_ai_and_bypasses_legacy_high_risk_review(db_session, monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "osr_escalation_orchestration_enabled", True, raising=False)
    add_hours(db_session, online=True)
    add_escalation(db_session, "compensation", "compensation", max_ai_attempts=2)
    ticket, conversation, visitor, _turn = make_webchat_case(db_session, body="I want compensation", suffix="continue-ai")
    db_session.commit()

    def fake_legacy(*_args, **_kwargs):
        return {"status": "done", "message_id": None, "reply_source": "legacy_test_runtime"}

    monkeypatch.setattr(webchat_ai_safe_service, "_legacy_process_webchat_ai_reply_job", fake_legacy)

    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)

    assert result["status"] == "done"
    assert result["reply_source"] == "legacy_test_runtime"
    assert db_session.query(WebchatHandoffRequest).count() == 0


def test_osr_escalation_audit_payload_redacts_customer_pii_tracking_and_raw_fields(db_session, monkeypatch):
    monkeypatch.setattr(webchat_ai_safe_service.settings, "osr_escalation_orchestration_enabled", True, raising=False)
    add_hours(db_session, online=False)
    add_escalation(db_session, "legal_threat", "legal", max_ai_attempts=0)
    raw_tracking = "CH020000123456"
    raw_email = "customer@example.com"
    raw_phone = "+382 68123456"
    body = f"legal issue for {raw_tracking}, email {raw_email}, phone {raw_phone}"
    ticket, conversation, visitor, _turn = make_webchat_case(db_session, body=body, suffix="redaction")
    db_session.commit()

    result = run_hook(db_session, ticket=ticket, conversation=conversation, visitor=visitor)
    rows = db_session.query(RuntimeDecisionAuditRecord).all()
    serialized = json.dumps([
        {"decision": row.decision_json, "case_context": row.case_context_json, "violations": row.violations_json, "warnings": row.warnings_json}
        for row in rows
    ], ensure_ascii=False, default=str)

    assert result["reason"] == "osr_ticket_created"
    assert raw_tracking not in serialized
    assert raw_email not in serialized
    assert raw_phone not in serialized
    assert "raw_prompt" not in serialized
    assert "raw_provider_payload" not in serialized
    assert "raw_tool_payload" not in serialized
    assert "[redacted_email]" in serialized
    assert "[redacted_phone]" in serialized
