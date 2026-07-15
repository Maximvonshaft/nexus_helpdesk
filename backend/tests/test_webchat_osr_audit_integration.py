from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_webchat_audit_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackgroundJob, User  # noqa: E402
from app.models_osr import CaseContextRecord, RuntimeDecisionAuditRecord  # noqa: E402
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_webchat_ai_reply_jobs  # noqa: E402
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult  # noqa: E402
from app.services.webchat_debug_bundle_service import build_ai_debug_bundle  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage  # noqa: E402


def _ensure_schema_and_user() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        columns = {col["name"] for col in inspect(conn).get_columns("webchat_ai_turns")}
        if "runtime_trace_json" not in columns:
            conn.execute(text("ALTER TABLE webchat_ai_turns ADD COLUMN runtime_trace_json TEXT"))
        outbound_columns = {col["name"] for col in inspect(conn).get_columns("ticket_outbound_messages")}
        outbound_contract_columns = {
            "origin": "VARCHAR(40)",
            "runtime_trace_id": "VARCHAR(120)",
            "runtime_contract_version": "VARCHAR(80)",
            "runtime_signature": "VARCHAR(128)",
            "runtime_contract_payload_json": "TEXT",
            "runtime_contract_payload_sha256": "VARCHAR(64)",
            "runtime_reply_type": "VARCHAR(40)",
            "safety_status": "VARCHAR(40)",
        }
        for column_name, column_type in outbound_contract_columns.items():
            if column_name not in outbound_columns:
                conn.execute(text(f"ALTER TABLE ticket_outbound_messages ADD COLUMN {column_name} {column_type}"))
        ticket_columns = {col["name"] for col in inspect(conn).get_columns("tickets")}
        for column_name, column_type in {"last_ai_update": "TEXT", "last_runtime_reply_at": "DATETIME"}.items():
            if column_name not in ticket_columns:
                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {column_name} {column_type}"))
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 99001).first():
            db.add(User(id=99001, username="webchat_osr_audit_admin", display_name="WebChat OSR Audit Admin", password_hash="test", role=UserRole.admin, is_active=True))
            db.commit()
    finally:
        db.close()


def _init_conversation(client: TestClient, suffix: str):
    init = client.post('/api/webchat/init', json={
        'tenant_key': f'osr-audit-{suffix}',
        'channel_key': 'website',
        'visitor_name': f'OSR Audit Visitor {suffix}',
        'origin': 'https://example.test',
        'page_url': 'https://example.test/help',
    })
    assert init.status_code == 200, init.text
    payload = init.json()
    return payload['conversation_id'], payload['visitor_token']


def _send(client: TestClient, conversation_id: str, visitor_token: str, body: str, client_message_id: str):
    res = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={'body': body, 'client_message_id': client_message_id},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _run_ai_turn(ai_turn_id: int, worker: str = 'osr-audit-worker'):
    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        job.next_run_at = None
        db.commit()
        processed = dispatch_pending_webchat_ai_reply_jobs(db, worker_id=worker)
        assert any(item.job_type == WEBCHAT_AI_REPLY_JOB for item in processed)
        db.commit()
    finally:
        db.close()


def _agent_message(conversation_id: str) -> WebchatMessage:
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        message = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == 'agent')
            .order_by(WebchatMessage.id.desc())
            .first()
        )
        assert message is not None
        db.expunge(message)
        return message
    finally:
        db.close()


def test_webchat_ai_job_worker_uses_orchestration_service_entrypoint():
    from app.services import background_jobs
    from app.services.webchat_ai_orchestration_service import process_webchat_ai_reply_job

    assert background_jobs.process_background_job.__globals__["WEBCHAT_AI_REPLY_JOB"] == "webchat.ai_reply"
    assert process_webchat_ai_reply_job.__module__ == "app.services.webchat_ai_orchestration_service"


def test_webchat_osr_audit_persists_allowed_tracking_decision_without_body_change(monkeypatch):
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client, 'trusted')
    sent = _send(client, conversation_id, visitor_token, 'Where is CH020000129135?', 'osr-audit-trusted-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_orchestration_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')

    def fake_tracking_fact(**_kwargs):
        return TrackingFactResult(
            ok=True,
            tracking_number='CH020000129135',
            status='OUT_FOR_DELIVERY',
            status_label='Out for delivery',
            checked_at='2026-07-09T08:00:00Z',
            tool_status='success',
            pii_redacted=True,
            fact_evidence_present=True,
            latest_event=TrackingFactEvent(description='Out for delivery'),
        )

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 30
        webchat_ai_service._LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = 12
        webchat_ai_service._LAST_BRIDGE_WAIT_TIMEOUT_MS = 12000
        webchat_ai_service._LAST_RUNTIME_TRACE = {
            'ai_decision_policy_ok': True,
            'ai_decision_intent': 'tracking',
            'ai_decision_next_action': 'reply',
        }
        webchat_ai_service._LAST_RUNTIME_RAG_TRACE = None
        return 'Your parcel is out for delivery.'

    monkeypatch.setattr(webchat_ai_service, '_maybe_lookup_tracking_fact', fake_tracking_fact)
    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

    _run_ai_turn(ai_turn_id)
    message = _agent_message(conversation_id)
    assert message.body == 'Your parcel is out for delivery.'

    db = SessionLocal()
    try:
        turn = db.query(WebchatAITurn).filter(WebchatAITurn.id == ai_turn_id).first()
        assert turn is not None
        audit = db.query(RuntimeDecisionAuditRecord).filter(RuntimeDecisionAuditRecord.ticket_id == message.ticket_id, RuntimeDecisionAuditRecord.conversation_id == message.conversation_id).order_by(RuntimeDecisionAuditRecord.id.desc()).first()
        assert audit is not None
        assert audit.allowed is True
        assert audit.business_reply_type == 'tracking_status_answer'
        context = db.query(CaseContextRecord).filter(CaseContextRecord.ticket_id == message.ticket_id, CaseContextRecord.conversation_id == message.conversation_id).first()
        assert context is not None
        assert context.tracking_number_hash
        metadata = json.loads(message.metadata_json or '{}')
        assert metadata['osr_audit']['mode'] == 'audit_only'
        assert metadata['osr_audit']['audit_id'] == audit.id
        assert metadata['osr_audit']['allowed'] is True
        metadata_text = json.dumps(metadata, ensure_ascii=False)
        assert 'CH020000129135' not in metadata_text
        bundle, _debug_run = build_ai_debug_bundle(db, turn=turn)
        assert bundle['osr']['mode'] == 'audit_only'
        assert bundle['osr']['audit_id'] == audit.id
        timeline_types = {item.get('event_type') for item in bundle.get('timeline', [])}
        assert 'osr.runtime_decision.audited' in timeline_types
    finally:
        db.close()


def test_webchat_osr_audit_without_fact_uses_clarification_not_factual_tracking(monkeypatch):
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client, 'nofact')
    sent = _send(client, conversation_id, visitor_token, 'Please help me track my parcel.', 'osr-audit-nofact-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_orchestration_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')
    monkeypatch.setattr(webchat_ai_service, '_maybe_lookup_tracking_fact', lambda **_kwargs: None)

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 18
        webchat_ai_service._LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = 12
        webchat_ai_service._LAST_BRIDGE_WAIT_TIMEOUT_MS = 12000
        webchat_ai_service._LAST_RUNTIME_TRACE = {
            'ai_decision_policy_ok': True,
            'ai_decision_intent': 'tracking_missing_number',
            'ai_decision_next_action': 'ask_clarifying_question',
        }
        return 'Please share the parcel number so I can check the latest status.'

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

    _run_ai_turn(ai_turn_id, worker='osr-audit-nofact-worker')
    message = _agent_message(conversation_id)
    assert message.body == 'Please share the parcel number so I can check the latest status.'

    db = SessionLocal()
    try:
        audit = db.query(RuntimeDecisionAuditRecord).filter(RuntimeDecisionAuditRecord.ticket_id == message.ticket_id, RuntimeDecisionAuditRecord.conversation_id == message.conversation_id).order_by(RuntimeDecisionAuditRecord.id.desc()).first()
        assert audit is not None
        assert audit.business_reply_type == 'clarification'
        assert audit.allowed is True
        decision_text = json.dumps(audit.decision_json, ensure_ascii=False)
        assert 'previous_ai_reply' not in decision_text
        assert 'customer_claim_used_as_fact' not in json.dumps(audit.violations_json or [], ensure_ascii=False)
    finally:
        db.close()


def test_webchat_runtime_contract_blocks_declared_unsupported_tracking_claim(monkeypatch):
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client, 'nofact-live')
    sent = _send(client, conversation_id, visitor_token, 'Please track my parcel.', 'osr-audit-nofact-live-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_orchestration_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')
    monkeypatch.setattr(webchat_ai_service, '_maybe_lookup_tracking_fact', lambda **_kwargs: None)

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_RUNTIME_TRACE = {
            'ai_decision_intent': 'tracking',
            'ai_decision_next_action': 'reply',
            'ai_decision_policy_ok': False,
            'ai_decision_policy_violation_codes': 'tracking_status_without_trusted_fact',
        }
        return 'Your parcel is out for delivery.'

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

    _run_ai_turn(ai_turn_id, worker='osr-audit-nofact-live-worker')

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        assert db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == 'agent').count() == 0
        audit = db.query(RuntimeDecisionAuditRecord).filter(RuntimeDecisionAuditRecord.ticket_id == conversation.ticket_id, RuntimeDecisionAuditRecord.conversation_id == conversation.id).order_by(RuntimeDecisionAuditRecord.id.desc()).first()
        assert audit is not None
        assert audit.business_reply_type == 'no_answer'
        assert audit.next_action == 'block'
        assert audit.allowed is True
        assert audit.violations_json in (None, [])
        context = db.query(CaseContextRecord).filter(CaseContextRecord.ticket_id == conversation.ticket_id, CaseContextRecord.conversation_id == conversation.id).first()
        assert context is not None
        assert context.issue_type == 'tracking'
    finally:
        db.close()


def test_webchat_osr_audit_failure_does_not_block_customer_visible_reply(monkeypatch):
    _ensure_schema_and_user()
    client = TestClient(app)
    conversation_id, visitor_token = _init_conversation(client, 'failure')
    sent = _send(client, conversation_id, visitor_token, 'Hello', 'osr-audit-failure-1')
    ai_turn_id = sent['ai_turn_id']

    from app.services import webchat_ai_orchestration_service, webchat_ai_service
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_RUNTIME_TRACE = {
            'ai_decision_policy_ok': True,
            'ai_decision_intent': 'general_support',
            'ai_decision_next_action': 'ask_clarifying_question',
        }
        return 'Hello, how can I help?'

    def raising_audit(db, **_kwargs):
        db.add(RuntimeDecisionAuditRecord(next_action='reply', decision_json={}))
        db.flush()

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)
    monkeypatch.setattr(webchat_ai_orchestration_service, 'audit_completed_webchat_ai_turn', raising_audit)

    _run_ai_turn(ai_turn_id, worker='osr-audit-failure-worker')
    message = _agent_message(conversation_id)
    assert message.body == 'Hello, how can I help?'

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == f'webchat-ai-turn:{ai_turn_id}').first()
        assert job is not None
        assert getattr(job.status, 'value', job.status) == 'done'
        assert db.query(RuntimeDecisionAuditRecord).filter(RuntimeDecisionAuditRecord.ticket_id == message.ticket_id, RuntimeDecisionAuditRecord.conversation_id == message.conversation_id).count() == 0
        assert db.query(WebchatMessage).filter(WebchatMessage.id == message.id).first().body == 'Hello, how can I help?'
    finally:
        db.close()
