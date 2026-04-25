from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app.enums import UserRole
from app.models import BackgroundJob, User
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_background_jobs
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


def test_public_webchat_init_send_poll_and_background_ai_reply(monkeypatch):
    client = TestClient(app)
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

    sent = client.post(f'/api/webchat/conversations/{conversation_id}/messages', json={
        'visitor_token': visitor_token,
        'body': 'Where is my parcel?',
    })
    assert sent.status_code == 200, sent.text

    polled = client.get(f'/api/webchat/conversations/{conversation_id}/messages', params={'visitor_token': visitor_token})
    assert polled.status_code == 200, polled.text
    messages_before = polled.json()['messages']
    assert any(item['direction'] == 'visitor' and item['body'] == 'Where is my parcel?' for item in messages_before)
    assert any(item['direction'] == 'agent' and 'received your parcel inquiry' in item['body'] for item in messages_before)

    from app.services import webchat_ai_service

    monkeypatch.setattr(webchat_ai_service, '_generate_ai_reply', lambda **kwargs: 'Please provide your tracking number so our support team can check your shipment.')

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        jobs = db.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.status == 'pending').all()
        assert jobs
        assert any(job.payload_json for job in jobs)
        processed = dispatch_pending_background_jobs(db, worker_id='pytest-worker')
        assert any(job.job_type == WEBCHAT_AI_REPLY_JOB for job in processed)
        db.commit()
    finally:
        db.close()

    polled_after = client.get(f'/api/webchat/conversations/{conversation_id}/messages', params={'visitor_token': visitor_token})
    assert polled_after.status_code == 200, polled_after.text
    messages_after = polled_after.json()['messages']
    assert any(item['direction'] == 'agent' and item['author_label'] == 'NexusDesk AI Assistant' for item in messages_after)
    assert any('tracking number' in item['body'].lower() for item in messages_after if item['direction'] == 'agent')
