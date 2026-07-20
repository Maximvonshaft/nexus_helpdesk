from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app.enums import UserRole
from app.models import BackgroundJob, User
from app.services import background_jobs
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB
from app.services.tracking_fact_schema import TrackingFactResult
from app.services.webchat_runtime_ai_service import WebchatRuntimeReplyResult
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: F401 - ensure metadata registration


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 9999).first():
            db.add(User(id=9999, username="roundb_admin", display_name="Round B Admin", password_hash="test", role=UserRole.admin, is_active=True))
            db.commit()
    finally:
        db.close()


def _create_webchat_message_flow(client: TestClient):
    init = client.post('/api/webchat/init', json={
        'tenant_key': 'pytest',
        'channel_key': 'website',
        'visitor_name': 'Pytest Visitor',
        'origin': 'https://example.test',
        'page_url': 'https://example.test/help',
    })
    assert init.status_code == 200, init.text
    payload = init.json()
    conversation_id = payload['conversation_id']
    visitor_token = payload['visitor_token']
    assert conversation_id.startswith('wc_')
    assert visitor_token

    sent = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'visitor_token': visitor_token,
            'body': 'Hello, what can you help me with?',
        },
    )
    assert sent.status_code == 200, sent.text

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'visitor_token': visitor_token},
    )
    assert polled.status_code == 200, polled.text
    messages_before = polled.json()['messages']
    assert any(item['direction'] == 'visitor' and 'Hello, what can you help me with?' in item['body'] for item in messages_before)
    assert not any(item['direction'] == 'agent' for item in messages_before)
    return conversation_id, visitor_token


def test_public_webchat_init_send_poll_and_background_ai_reply(monkeypatch):
    client = TestClient(app)
    conversation_id, visitor_token = _create_webchat_message_flow(client)

    from app.services import conversation_ai_service
    from app.services import webchat_ai_orchestration_service

    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')
    monkeypatch.setattr(
        conversation_ai_service,
        'lookup_tracking_fact',
        lambda **_kwargs: TrackingFactResult(
            ok=False,
            tool_status='skipped',
            pii_redacted=True,
            failure_reason='missing_tracking_number',
        ),
    )
    monkeypatch.setattr(
        conversation_ai_service,
        '_run_runtime',
        lambda **_kwargs: WebchatRuntimeReplyResult(
            ok=True,
            ai_generated=True,
            reply_source='private_ai_runtime',
            reply='I can help with shipment questions, delivery updates, and general support requests.',
            intent='general_support',
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=18,
            runtime_trace={
                'ai_decision_policy_ok': True,
                'ai_decision_intent': 'general_support',
                'ai_decision_next_action': 'reply',
            },
            tool_calls=[],
        ),
    )

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.status == 'pending').all()
        assert jobs
        current_job = jobs[-1]
        assert current_job.payload_json
        background_jobs.process_background_job(db, current_job)
        db.commit()
    finally:
        db.close()

    polled_after = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'visitor_token': visitor_token},
    )
    assert polled_after.status_code == 200, polled_after.text
    messages_after = polled_after.json()['messages']
    assert any(item['direction'] == 'agent' and item['author_label'] == 'AI Assistant' for item in messages_after)
    assert any('shipment' in item['body'].lower() or 'support' in item['body'].lower() for item in messages_after if item['direction'] == 'agent')
