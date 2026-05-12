from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.enums import SourceChannel
from app.services.email_provider import SandboxEmailProvider
from app.services.outbound_semantics import (
    external_channel_values,
    is_customer_outbound_channel,
    validate_customer_outbound_channel,
)
from app.services.reply_channel_policy import ReplyTargetError, resolve_ticket_reply_target


def test_customer_outbound_allows_email():
    assert is_customer_outbound_channel(SourceChannel.email)
    assert validate_customer_outbound_channel(SourceChannel.email) == SourceChannel.email


def test_customer_outbound_allows_whatsapp():
    assert is_customer_outbound_channel(SourceChannel.whatsapp)
    assert validate_customer_outbound_channel(SourceChannel.whatsapp) == SourceChannel.whatsapp


@pytest.mark.parametrize('channel', [SourceChannel.web_chat, SourceChannel.telegram, SourceChannel.sms, SourceChannel.internal])
def test_customer_outbound_rejects_non_target_channels(channel):
    assert not is_customer_outbound_channel(channel)
    with pytest.raises(ValueError):
        validate_customer_outbound_channel(channel)


def test_external_worker_channels_are_email_and_whatsapp_only():
    assert external_channel_values() == ['email', 'whatsapp']


def test_webchat_reply_target_requires_email_contact():
    ticket = SimpleNamespace(
        source_channel=SourceChannel.web_chat,
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact=None,
        source_chat_id='wc_123',
    )
    with pytest.raises(ReplyTargetError) as exc:
        resolve_ticket_reply_target(ticket)
    assert exc.value.code == 'customer_email_required_for_webchat_intake'


def test_webchat_reply_target_resolves_email_only():
    ticket = SimpleNamespace(
        source_channel=SourceChannel.web_chat,
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact='customer@example.com',
        source_chat_id='wc_123',
    )
    target = resolve_ticket_reply_target(ticket)
    assert target.channel == SourceChannel.email
    assert target.contact == 'customer@example.com'


def test_whatsapp_reply_target_requires_whatsapp_channel():
    ticket = SimpleNamespace(
        source_channel=SourceChannel.whatsapp,
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact='+410000000',
        source_chat_id='whatsapp:+410000000',
    )
    with pytest.raises(ReplyTargetError) as exc:
        resolve_ticket_reply_target(ticket)
    assert exc.value.code == 'whatsapp_reply_channel_required'


def test_auto_reply_policy_does_not_fallback_webchat_to_whatsapp():
    ticket = SimpleNamespace(
        source_channel=SourceChannel.web_chat,
        preferred_reply_channel=None,
        preferred_reply_contact=None,
        source_chat_id='wc_123',
    )
    with pytest.raises(ReplyTargetError) as exc:
        resolve_ticket_reply_target(ticket)
    assert exc.value.code == 'customer_email_required_for_webchat_intake'


def test_sandbox_email_success_sets_provider_message_id():
    message = SimpleNamespace(id=42, ticket_id=7, body='hello customer')
    result = SandboxEmailProvider().dispatch(message)
    assert result.status == 'sent'
    assert result.provider == 'sandbox_email'
    assert result.provider_message_id == 'sandbox_email_42'
    assert result.provider_thread_id == 'sandbox_thread_7'


def test_sandbox_email_failure_does_not_return_provider_message_id():
    message = SimpleNamespace(id=43, ticket_id=7, body='SANDBOX_FAIL')
    result = SandboxEmailProvider().dispatch(message)
    assert result.status == 'failed'
    assert result.error_code == 'sandbox_failure'
    assert result.provider_message_id is None
