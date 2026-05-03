from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.enums import MessageStatus, SourceChannel, UserRole
from app.main import app
from app.models import TicketOutboundMessage, User
from app.services.outbound_semantics import (
    is_external_outbound_channel,
    is_external_outbound_message,
    is_webchat_local_only_message,
    outbound_ui_label,
    count_outbound_semantics,
)
from app.settings import get_settings
from app.services.webchat_fact_gate import evaluate_webchat_fact_gate
from app.webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage  # noqa: F401 - ensure metadata registration
from app.webchat_schemas import WebChatActionSubmitRequest, WebChatCardAction, WebChatCardPayload


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


def _conversation_for(public_id: str) -> WebchatConversation:
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
        assert conversation is not None
        db.expunge(conversation)
        return conversation
    finally:
        db.close()


def test_webchat_legacy_static_quick_replies_mode_still_generates_card(monkeypatch):
    monkeypatch.setenv("WEBCHAT_STATIC_QUICK_REPLIES_MODE", "legacy")
    get_settings.cache_clear()
    
    try:
        client = TestClient(app)
        conversation_id, visitor_token, client_message_id = _init_and_send(client, body="Hello")

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
        
        qr_card = next(item for item in data['messages'] if item['message_type'] == 'card' and item['payload_json']['card_type'] == 'quick_replies')
        assert qr_card['payload_json']['title'] == 'How can we help you?'
        actions = [a['id'] for a in qr_card['payload_json']['actions']]
        assert 'track_parcel' in actions
        assert 'change_address' in actions
        assert 'talk_to_human' in actions

        next_after_id = data['next_after_id']
        second_poll = client.get(
            f'/api/webchat/conversations/{conversation_id}/messages',
            headers={'X-Webchat-Visitor-Token': visitor_token},
            params={'after_id': next_after_id, 'limit': 20},
        )
        assert second_poll.status_code == 200, second_poll.text
        assert second_poll.json()['messages'] == []
    finally:
        get_settings.cache_clear()

def test_webchat_default_does_not_generate_static_quick_replies():
    get_settings.cache_clear()
    client = TestClient(app)
    conversation_id, visitor_token, client_message_id = _init_and_send(client, body="Hello")

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 20},
    )
    assert polled.status_code == 200
    data = polled.json()
    assert any(item['message_type'] == 'text' and item['client_message_id'] == client_message_id for item in data['messages'])
    assert not any(item['message_type'] == 'card' and item['payload_json']['card_type'] == 'quick_replies' for item in data['messages'])

def test_webchat_default_tracking_does_not_generate_static_quick_replies():
    get_settings.cache_clear()
    client = TestClient(app)
    conversation_id, visitor_token, client_message_id = _init_and_send(client, body="I want to track my parcel")

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 20},
    )
    assert polled.status_code == 200
    data = polled.json()
    assert not any(item['message_type'] == 'card' and item['payload_json']['card_type'] == 'quick_replies' for item in data['messages'])

def test_webchat_default_unknown_does_not_generate_static_quick_replies():
    get_settings.cache_clear()
    client = TestClient(app)
    conversation_id, visitor_token, client_message_id = _init_and_send(client, body="some random unknown thing")

    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 20},
    )
    assert polled.status_code == 200
    data = polled.json()
    assert not any(item['message_type'] == 'card' and item['payload_json']['card_type'] == 'quick_replies' for item in data['messages'])


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


def test_quick_reply_action_submit_records_action_and_ticket_event(monkeypatch):
    monkeypatch.setenv("WEBCHAT_STATIC_QUICK_REPLIES_MODE", "legacy")
    get_settings.cache_clear()
    try:
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
    finally:
        get_settings.cache_clear()


def test_invalid_card_type_and_invalid_action_id_rejected(monkeypatch):
    monkeypatch.setenv("WEBCHAT_STATIC_QUICK_REPLIES_MODE", "legacy")
    get_settings.cache_clear()

    try:
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
    finally:
        get_settings.cache_clear()


def test_webchat_card_payload_rejects_html_script_iframe_style_and_unsafe_urls():
    unsafe_texts = [
        '<script>alert(1)</script>',
        '<img src=x onerror=alert(1)>',
        '<iframe src="https://evil.example"></iframe>',
        '<style>body{}</style>',
        'javascript:alert(1)',
    ]
    for text in unsafe_texts:
        with pytest.raises(ValueError):
            WebChatCardPayload(
                card_id='card_safe_1',
                card_type='quick_replies',
                title=text,
                body='Choose one',
                actions=[WebChatCardAction(id='track_parcel', label='Track parcel', value='track')],
            )

    with pytest.raises(ValueError):
        WebChatCardPayload(
            card_id='card_safe_2',
            card_type='quick_replies',
            title='Safe',
            body='Choose one',
            actions=[WebChatCardAction(id='track_parcel', label='Track parcel', value='track', payload={'target_url': 'http://evil.example'})],
        )

    payload = WebChatCardPayload(
        card_id='card_safe_3',
        card_type='quick_replies',
        title='Safe',
        body='Choose one',
        actions=[WebChatCardAction(id='track_parcel', label='Track parcel', value='track', payload={'target_url': 'https://example.test/help'})],
    )
    assert payload.card_id == 'card_safe_3'


def test_webchat_action_submit_rejects_unsafe_ids():
    for unsafe_id in ['card_<script>', 'card_ bad', 'card_"quote', "card_'quote", 'card_/path', 'card_\\path']:
        with pytest.raises(ValueError):
            WebChatActionSubmitRequest(message_id=1, card_id=unsafe_id, action_id='track_parcel', action_type='quick_reply')
    for unsafe_action in ['bad action', '<script>', 'bad"quote', "bad'quote", 'bad/path']:
        with pytest.raises(ValueError):
            WebChatActionSubmitRequest(message_id=1, card_id='card_quick_abc123', action_id=unsafe_action, action_type='quick_reply')
    assert WebChatActionSubmitRequest(message_id=1, card_id='card_quick_abc123', action_id='request_handoff', action_type='handoff_request')


def test_fact_gate_blocks_unverified_operational_claims():
    for text in ['Your parcel was delivered today', 'Refund approved', 'Address changed successfully', 'Customs cleared']:
        decision = evaluate_webchat_fact_gate(text, fact_evidence_present=False)
        assert decision.allowed is False
        assert decision.fact_evidence_present is False
    assert evaluate_webchat_fact_gate('Please share your tracking number', fact_evidence_present=False).allowed is True


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

    conversation = _conversation_for(conversation_id)
    db = SessionLocal()
    try:
        rows = db.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == conversation.ticket_id).all()
        assert any(row.provider_status == 'webchat_handoff_ack_delivered' for row in rows)
        webchat_rows = [row for row in rows if row.channel == SourceChannel.web_chat]
        assert webchat_rows
        assert all(not is_external_outbound_message(row) for row in webchat_rows)
        assert all(is_webchat_local_only_message(row) or row.provider_status is None for row in webchat_rows)
        counts = count_outbound_semantics(db)
        assert counts['webchat_handoff_ack_sent'] >= 1
    finally:
        db.close()


def test_outbound_semantics_labels_and_external_channels():
    assert not is_external_outbound_channel(SourceChannel.web_chat)
    assert is_external_outbound_channel(SourceChannel.whatsapp)
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_safe_ack_delivered') == 'Local WebChat ACK'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_ai_delivered') == 'Local WebChat AI Reply'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_ai_safe_fallback') == 'WebChat Safe Fallback'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_card_delivered') == 'Local WebChat Card'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_handoff_ack_delivered') == 'Local WebChat Handoff ACK'
    assert outbound_ui_label(SourceChannel.whatsapp, MessageStatus.pending, 'queued') == 'External Send Pending'
    assert outbound_ui_label(SourceChannel.email, MessageStatus.sent, 'sent') == 'External Send Sent'
    assert outbound_ui_label(SourceChannel.telegram, MessageStatus.dead, 'dead') == 'External Send Failed'
