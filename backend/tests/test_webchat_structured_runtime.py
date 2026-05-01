from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.enums import MessageStatus, SourceChannel, UserRole
from app.main import app
from app.models import TicketOutboundMessage, User
from app.services.outbound_semantics import (
    is_external_outbound_channel,
    outbound_ui_label,
    count_outbound_semantics,
)
from app.webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage  # noqa: F401 - ensure metadata registration
from app.webchat_schemas import WebChatCardPayload


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 9911).first():
            db.add(User(id=9911, username="wc_struct_admin", display_name="WC Struct Admin", password_hash="test", role=UserRole.admin, is_active=True))
            db.commit()
    finally:
        db.close()


def _init_and_send(client: TestClient, body: str = "Hello, I need help tracking my parcel"):
    init = client.post('/api/webchat/init', json={
        'tenant_key': 'pytest-structured',
        'channel_key': 'website',
        'visitor_name': 'Structured Visitor',
        'origin': 'https://example.test',
        'page_url': 'https://example.test/help',
    })
    assert init.status_code == 200, init.text
    payload = init.json()
    conversation_id = payload['conversation_id']
    visitor_token = payload['visitor_token']
    client_message_id = 'pytest-client-message-1'
    sent = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={'body': body, 'client_message_id': client_message_id},
    )
    assert sent.status_code == 200, sent.text
    return conversation_id, visitor_token, client_message_id


def test_webchat_structured_message_contract_and_incremental_poll():
    client = TestClient(app)
    conversation_id, visitor_token, client_message_id = _init_and_send(client)

    first_poll = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 20},
    )
    assert first_poll.status_code == 200, first_poll.text
    data = first_poll.json()
    assert data['has_more'] is False
    assert any(item['message_type'] == 'text' and item['client_message_id'] == client_message_id for item in data['messages'])
    assert any(item['message_type'] == 'card' and item['payload_json']['card_type'] == 'quick_replies' for item in data['messages'])

    next_after_id = data['next_after_id']
    second_poll = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'after_id': next_after_id, 'limit': 20},
    )
    assert second_poll.status_code == 200, second_poll.text
    assert second_poll.json()['messages'] == []


def test_webchat_client_message_id_idempotency():
    client = TestClient(app)
    conversation_id, visitor_token, client_message_id = _init_and_send(client, body='Hello support')
    duplicate = client.post(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={'body': 'Hello support duplicate', 'client_message_id': client_message_id},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()['idempotent'] is True

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        rows = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.client_message_id == client_message_id).all()
        assert len(rows) == 1
    finally:
        db.close()


def test_quick_reply_action_submit_records_action_and_ticket_event():
    client = TestClient(app)
    conversation_id, visitor_token, _ = _init_and_send(client)
    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 30},
    )
    card = next(item for item in polled.json()['messages'] if item['message_type'] == 'card' and item['payload_json']['card_type'] == 'quick_replies')
    action = card['payload_json']['actions'][0]
    submitted = client.post(
        f'/api/webchat/conversations/{conversation_id}/actions',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'message_id': card['id'],
            'card_id': card['payload_json']['card_id'],
            'action_id': action['id'],
            'action_type': action['action_type'],
            'payload': action.get('payload') or {},
        },
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body['ok'] is True
    assert body['message']['message_type'] == 'action'

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        assert db.query(WebchatCardAction).filter(WebchatCardAction.conversation_id == conversation.id).count() == 1
    finally:
        db.close()


def test_invalid_card_type_and_invalid_action_id_rejected():
    with pytest.raises(ValueError):
        WebChatCardPayload(card_id='card_bad', card_type='evil_html', title='Bad', body='Bad', actions=[])

    client = TestClient(app)
    conversation_id, visitor_token, _ = _init_and_send(client)
    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 30},
    )
    card = next(item for item in polled.json()['messages'] if item['message_type'] == 'card')
    rejected = client.post(
        f'/api/webchat/conversations/{conversation_id}/actions',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'message_id': card['id'],
            'card_id': card['payload_json']['card_id'],
            'action_id': 'not_in_card',
            'action_type': 'quick_reply',
            'payload': {},
        },
    )
    assert rejected.status_code == 400


def test_handoff_card_and_action_are_local_only_outbound():
    client = TestClient(app)
    conversation_id, visitor_token, _ = _init_and_send(client, body='I want a human support agent for a complaint')
    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 30},
    )
    card = next(item for item in polled.json()['messages'] if item['message_type'] == 'card' and item['payload_json']['card_type'] == 'handoff')
    action = card['payload_json']['actions'][0]
    submitted = client.post(
        f'/api/webchat/conversations/{conversation_id}/actions',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'message_id': card['id'],
            'card_id': card['payload_json']['card_id'],
            'action_id': action['id'],
            'action_type': action['action_type'],
            'payload': action.get('payload') or {},
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()['handoff_triggered'] is True

    db = SessionLocal()
    try:
        counts = count_outbound_semantics(db)
        assert counts['webchat_handoff_ack_sent'] >= 1
        assert counts['external_pending_outbound'] == 0
    finally:
        db.close()


def test_outbound_semantics_labels_and_external_channels():
    assert not is_external_outbound_channel(SourceChannel.web_chat)
    assert is_external_outbound_channel(SourceChannel.whatsapp)
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_safe_ack_delivered') == 'Local WebChat ACK'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_ai_safe_fallback') == 'WebChat Safe Fallback'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_card_delivered') == 'Local WebChat Card'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_handoff_ack_delivered') == 'Local WebChat Handoff ACK'
    assert outbound_ui_label(SourceChannel.whatsapp, MessageStatus.pending, 'queued') == 'External Send Pending'
    assert outbound_ui_label(SourceChannel.email, MessageStatus.sent, 'sent') == 'External Send Sent'
    assert outbound_ui_label(SourceChannel.telegram, MessageStatus.dead, 'dead') == 'External Send Failed'
