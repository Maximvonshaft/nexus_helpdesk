import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.integration import _contact_match_filters, _customer_contact_filters, _normalize_channel, _ticket_duplicate_contact_filters  # noqa: E402
from app.enums import SourceChannel  # noqa: E402


def test_integration_channel_accepts_supported_values():
    assert _normalize_channel('whatsapp') == SourceChannel.whatsapp
    assert _normalize_channel('email') == SourceChannel.email
    assert _normalize_channel('web') == SourceChannel.web_chat
    assert _normalize_channel('web_chat') == SourceChannel.web_chat
    assert _normalize_channel('chat') == SourceChannel.web_chat


def test_integration_channel_rejects_unknown_values():
    with pytest.raises(HTTPException) as exc:
        _normalize_channel('whatapp')
    assert exc.value.status_code == 400


def test_contact_profile_filters_include_normalized_phone_and_email():
    phone_filters = _contact_match_filters('+41 79 123 45 67')
    assert len(phone_filters) >= 8
    email_filters = _customer_contact_filters('Customer@Example.COM')
    assert len(email_filters) >= 4


def test_duplicate_contact_filters_do_not_add_null_contact_match():
    filters = _ticket_duplicate_contact_filters('customer@example.com')
    # raw preferred_reply_contact + raw source_chat_id only; no normalized-phone NULL comparisons.
    assert len(filters) == 2


def test_duplicate_contact_filters_include_normalized_phone_when_distinct():
    filters = _ticket_duplicate_contact_filters('+41 79 123 45 67')
    assert len(filters) == 4
