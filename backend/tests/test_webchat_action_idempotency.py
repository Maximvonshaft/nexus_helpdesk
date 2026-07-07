from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage
from app.webchat_schemas import WebChatCardAction, WebChatCardPayload


def _insert_legacy_card(conversation_id: str) -> dict:
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        payload = WebChatCardPayload(
            card_id="card_legacy_idempotency",
            card_type="handoff",
            title="",
            body="",
            actions=[
                WebChatCardAction(id="escalate", label="Escalate", value="escalate", action_type="handoff_request", payload={"intent": "handoff"}),
            ],
        )
        message = WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            direction="system",
            body="",
            body_text="",
            message_type="card",
            payload_json=payload.model_dump_json(),
            delivery_status="sent",
            author_label="System",
        )
        db.add(message)
        db.commit()
        return {"id": message.id, "payload_json": payload.model_dump(mode="json")}
    finally:
        db.close()


def test_webchat_action_submit_is_idempotent_for_existing_legacy_card():
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

    card = _insert_legacy_card(conversation_id)
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
