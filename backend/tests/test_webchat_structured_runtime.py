from __future__ import annotations

import json

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
)
from app.settings import get_settings
from app.services.webchat_fact_gate import evaluate_webchat_fact_gate
from app.services.webchat_runtime_output_parser import RuntimeReplyParseError, parse_runtime_reply_provider_output
from app.webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage  # noqa: F401 - ensure metadata registration
from app.webchat_schemas import WebChatActionSubmitRequest, WebChatCardAction, WebChatCardPayload

RETIRED_CARD_TYPE = "quick" + "_replies"
RETIRED_ACTION_TYPE = "quick" + "_reply"


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


def _insert_retired_action_card(public_id: str) -> dict:
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
        assert conversation is not None
        payload = {
            "card_id": f"card_legacy_{public_id}",
            "card_type": RETIRED_CARD_TYPE,
            "version": 1,
            "title": "",
            "body": "",
            "actions": [
                {"id": "lookup", "label": "Lookup", "value": "lookup", "action_type": RETIRED_ACTION_TYPE, "payload": {"intent": "tracking"}},
                {"id": "talk_to_human", "label": "Talk to support", "value": "talk_to_human", "action_type": "handoff_request", "payload": {"intent": "handoff"}},
            ],
            "metadata": {},
        }
        message = WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            direction="system",
            body="",
            body_text="",
            message_type="card",
            payload_json=json.dumps(payload, ensure_ascii=False),
            delivery_status="sent",
            author_label="System",
        )
        db.add(message)
        db.commit()
        return {"id": message.id, "payload_json": payload}
    finally:
        db.close()


def test_runtime_parser_cleans_mixed_waybill_label():
    for raw, expected in (
        ("您的包裹的waybill号运单尾号 129135已经提供了，但目前还没有找到有效的验证结果。", "运单尾号 129135"),
        ("请确认一下这个waybill号码是否完整且正确。", "这个运单号是否完整"),
        ("我看到您的包裹Waybill的最后几位是运单尾号 129135。", "包裹运单的最后几位"),
    ):
        parsed = parse_runtime_reply_provider_output(
            {
                "customer_reply": raw,
                "language": "zh",
                "intent": "tracking_unresolved",
                "handoff_required": False,
                "ticket_should_create": False,
            },
            evidence_present=False,
        )

        assert "waybill号" not in parsed.reply
        assert "waybill号码" not in parsed.reply
        assert "Waybill" not in parsed.reply
        assert expected in parsed.reply


def _insert_legacy_handoff_card(public_id: str) -> dict:
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
        assert conversation is not None
        payload = WebChatCardPayload(
            card_id=f"card_handoff_{public_id}",
            card_type="handoff",
            title="",
            body="",
            actions=[
                WebChatCardAction(id="talk_to_human", label="Talk to support", value="talk_to_human", action_type="handoff_request", payload={"intent": "handoff"}),
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


def test_webchat_static_action_cards_generation_is_retired():
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
        assert not any(item['message_type'] == 'card' for item in data['messages'])

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

def test_webchat_default_does_not_generate_static_action_cards():
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
    assert not any(item['message_type'] == 'card' for item in data['messages'])

def test_webchat_default_tracking_does_not_generate_static_action_cards():
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
    assert not any(item['message_type'] == 'card' for item in data['messages'])

def test_webchat_default_unknown_does_not_generate_static_action_cards():
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
    assert not any(item['message_type'] == 'card' for item in data['messages'])


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


def test_retired_action_card_submit_is_rejected():
    client = TestClient(app)
    conversation_id, visitor_token, _ = _init_and_send(client)
    card = _insert_retired_action_card(conversation_id)
    action = card['payload_json']['actions'][0]
    submitted = client.post(
        f'/api/webchat/conversations/{conversation_id}/actions',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'message_id': card['id'],
            'card_id': card['payload_json']['card_id'],
            'action_id': action['id'],
            'action_type': 'handoff_request',
            'payload': action.get('payload') or {},
        },
    )
    assert submitted.status_code == 410, submitted.text

    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_id).first()
        assert conversation is not None
        assert db.query(WebchatCardAction).filter(WebchatCardAction.conversation_id == conversation.id).count() == 0
    finally:
        db.close()


def test_invalid_card_type_and_invalid_action_id_rejected():
    with pytest.raises(ValueError):
        WebChatCardPayload(card_id='card_bad', card_type='evil_html', title='Bad', body='Bad', actions=[])

    client = TestClient(app)
    conversation_id, visitor_token, _ = _init_and_send(client)
    card = _insert_legacy_handoff_card(conversation_id)
    rejected = client.post(
        f'/api/webchat/conversations/{conversation_id}/actions',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        json={
            'message_id': card['id'],
            'card_id': card['payload_json']['card_id'],
            'action_id': 'not_in_card',
            'action_type': 'handoff_request',
            'payload': {},
        },
    )
    assert rejected.status_code == 400


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
                card_type='handoff',
                title=text,
                body='Choose one',
                actions=[WebChatCardAction(id='lookup', label='Lookup', value='lookup', action_type='handoff_request')],
            )

    with pytest.raises(ValueError):
        WebChatCardPayload(
            card_id='card_safe_2',
            card_type='handoff',
            title='Safe',
            body='Choose one',
            actions=[WebChatCardAction(id='lookup', label='Lookup', value='lookup', action_type='handoff_request', payload={'target_url': 'http://evil.example'})],
        )

    payload = WebChatCardPayload(
        card_id='card_safe_3',
        card_type='handoff',
        title='Safe',
        body='Choose one',
        actions=[WebChatCardAction(id='lookup', label='Lookup', value='lookup', action_type='handoff_request', payload={'target_url': 'https://example.test/help'})],
    )
    assert payload.card_id == 'card_safe_3'


def test_webchat_action_submit_rejects_unsafe_ids():
    for unsafe_id in ['card_<script>', 'card_ bad', 'card_"quote', "card_'quote", 'card_/path', 'card_\\path']:
        with pytest.raises(ValueError):
            WebChatActionSubmitRequest(message_id=1, card_id=unsafe_id, action_id='request_handoff', action_type='handoff_request')
    for unsafe_action in ['bad action', '<script>', 'bad"quote', "bad'quote", 'bad/path']:
        with pytest.raises(ValueError):
            WebChatActionSubmitRequest(message_id=1, card_id='card_handoff_abc123', action_id=unsafe_action, action_type='handoff_request')
    assert WebChatActionSubmitRequest(message_id=1, card_id='card_handoff_abc123', action_id='request_handoff', action_type='handoff_request')


def test_fact_gate_blocks_unverified_operational_claims():
    for text in ['Your parcel was delivered today', 'Refund approved', 'Address changed successfully', 'Customs cleared']:
        decision = evaluate_webchat_fact_gate(text, fact_evidence_present=False)
        assert decision.allowed is False
        assert decision.fact_evidence_present is False
    assert evaluate_webchat_fact_gate('Could you send the shipment reference', fact_evidence_present=False).allowed is True
    assert evaluate_webchat_fact_gate('Share the parcel number when you are ready and I will check the latest tracking details.', fact_evidence_present=False).allowed is True
    assert evaluate_webchat_fact_gate('I can check whether it is out for delivery after you send the tracking number.', fact_evidence_present=False).allowed is True


def test_runtime_parser_allows_shipment_outcome_only_with_tracking_evidence():
    payload = {
        "customer_reply": "Your parcel ending 007813 has been delivered.",
        "intent": "tracking",
        "tracking_number": None,
        "handoff_required": False,
    }

    with pytest.raises(RuntimeReplyParseError):
        parse_runtime_reply_provider_output(payload, evidence_present=False)

    parsed = parse_runtime_reply_provider_output(payload, evidence_present=True)
    assert parsed.reply.startswith("Your parcel ending")

    refund_payload = {
        "customer_reply": "Your refund has been approved and processed.",
        "intent": "tracking",
        "tracking_number": None,
        "handoff_required": False,
    }
    with pytest.raises(RuntimeReplyParseError):
        parse_runtime_reply_provider_output(refund_payload, evidence_present=True)


def test_explicit_human_text_does_not_generate_local_handoff_card_or_ack():
    client = TestClient(app)
    conversation_id, visitor_token, _ = _init_and_send(client, body='I want a human support agent for a complaint')
    polled = client.get(
        f'/api/webchat/conversations/{conversation_id}/messages',
        headers={'X-Webchat-Visitor-Token': visitor_token},
        params={'limit': 30},
    )
    assert polled.status_code == 200, polled.text
    assert not any(item['message_type'] == 'card' and item['payload_json']['card_type'] == 'handoff' for item in polled.json()['messages'])

    conversation = _conversation_for(conversation_id)
    db = SessionLocal()
    try:
        rows = db.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == conversation.ticket_id).all()
        webchat_rows = [row for row in rows if row.channel == SourceChannel.web_chat]
        assert not any(row.provider_status == 'webchat_handoff_ack_delivered' for row in rows)
        assert all(not is_external_outbound_message(row) for row in webchat_rows)
        assert all(is_webchat_local_only_message(row) or row.provider_status is None for row in webchat_rows)
    finally:
        db.close()


def test_outbound_semantics_labels_and_external_channels():
    assert not is_external_outbound_channel(SourceChannel.web_chat)
    assert is_external_outbound_channel(SourceChannel.whatsapp)
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_delivered') == 'Local WebChat ACK'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_ai_delivered') == 'Local WebChat AI Reply'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_card_delivered') == 'Local WebChat Card'
    assert outbound_ui_label(SourceChannel.web_chat, MessageStatus.sent, 'webchat_handoff_ack_delivered') == 'Local WebChat Handoff ACK'
    assert outbound_ui_label(SourceChannel.whatsapp, MessageStatus.pending, 'queued') == 'External Send Pending'
    assert outbound_ui_label(SourceChannel.email, MessageStatus.sent, 'sent') == 'External Send Sent'
    assert outbound_ui_label(SourceChannel.telegram, MessageStatus.dead, 'dead') == 'External Send Failed'
