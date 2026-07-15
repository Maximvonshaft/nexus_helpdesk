from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app.enums import UserRole
from app.models import BackgroundJob, User
from app.services import background_jobs
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB
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

    from app.services import webchat_ai_service
    from app.services import webchat_ai_orchestration_service

    monkeypatch.setattr(webchat_ai_orchestration_service.settings, 'webchat_ai_auto_reply_mode', 'runtime')
    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = 'private_ai_runtime'
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 18
        webchat_ai_service._LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = 12
        webchat_ai_service._LAST_BRIDGE_WAIT_TIMEOUT_MS = 12000
        webchat_ai_service._LAST_RUNTIME_HANDOFF_REQUIRED = False
        webchat_ai_service._LAST_RUNTIME_HANDOFF_REASON = None
        webchat_ai_service._LAST_RUNTIME_RECOMMENDED_AGENT_ACTION = None
        webchat_ai_service._LAST_RUNTIME_TRACE = {
            'ai_decision_policy_ok': True,
            'ai_decision_intent': 'general_support',
            'ai_decision_next_action': 'reply',
        }
        return 'I can help with shipment questions, delivery updates, and general support requests.'

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', fake_generate_ai_reply)

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
