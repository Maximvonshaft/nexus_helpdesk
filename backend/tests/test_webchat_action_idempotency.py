from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.settings import get_settings
from app.webchat_models import WebchatCardAction, WebchatConversation


def test_webchat_action_submit_is_idempotent_for_same_card_action(monkeypatch):
    monkeypatch.setenv("WEBCHAT_STATIC_QUICK_REPLIES_MODE", "legacy")
    get_settings.cache_clear()
    Base.metadata.create_all(bind=engine)
    client = TestClient(app)

    init = client.post('/api/webchat/init', json={
        'tenant_key': 'pytest-action-idempotency',
        'channel_key': 'website',
        'visitor_name': 'Action Idempotency Visitor',
        'origin': 'https://example.test',
        'page_url': 'https://example.test/help',
    })
    assert init.status_code == 200, init.text
    init_payload = init.json()
    conversation_id = init_payload['conversation_id']
    visitor_token = init_payload['visitor_token']

    sent = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={'body': 'Hello, I need help tracking my parcel', 'client_message_id': 'action-idempotency-msg-1'},
    )
    assert sent.status_code == 200, sent.text

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 30},
    )
    assert polled.status_code == 200, polled.text
    card = next(item for item in polled.json()['messages'] if item['message_type'] == 'card' and item['payload_json']['card_type'] == 'quick_replies')
    action = card['payload_json']['actions'][0]
    body = {
        'message_id': card['id'],
        'card_id': card['payload_json']['card_id'],
        'action_id': action['id'],
        'action_type': action['action_type'],
        'payload': action.get('payload') or {},
    }

    first = client.post(
        f'/api/webchat/conversations/{conversation_id}/actions',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json=body,
    )
    assert first.status_code == 200, first.text
    assert first.json()['ok'] is True
    assert first.json().get('idempotent') is not True

    second = client.post(
        f'/api/webchat/conversations/{conversation_id}/actions',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json=body,
    )
    assert second.status_code == 200, second.text
    assert second.json()['ok'] is True
    assert second.json()['idempotent'] is True
    assert second.json()['action_id'] == first.json()['action_id']
    assert second.json()['message']['message_type'] == 'action'

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        actions = db.query(WebchatCardAction).filter(
            WebchatCardAction.conversation_id == conversation.id,
            WebchatCardAction.message_id == card['id'],
        ).all()
        assert len(actions) == 1
    finally:
        db.close()
        get_settings.cache_clear()
