import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.integration_runtime import (  # noqa: E402
    _contact_match_filters,
    _customer_contact_filters,
    _normalize_channel,
    _ticket_duplicate_contact_filters,
)
from app.enums import SourceChannel  # noqa: E402


def _render(filters) -> str:
    return "\n".join(str(item) for item in filters).lower()


def test_integration_channel_accepts_supported_values():
    assert _normalize_channel("whatsapp") == SourceChannel.whatsapp
    assert _normalize_channel("email") == SourceChannel.email
    assert _normalize_channel("web") == SourceChannel.web_chat
    assert _normalize_channel("web_chat") == SourceChannel.web_chat
    assert _normalize_channel("chat") == SourceChannel.web_chat


def test_integration_channel_rejects_unknown_values():
    with pytest.raises(HTTPException) as exc:
        _normalize_channel("whatapp")
    assert exc.value.status_code == 400


def test_contact_profile_filters_include_normalized_phone_and_email():
    phone_filters = _contact_match_filters("+41 79 123 45 67")
    rendered_phone = _render(phone_filters)
    assert "phone_normalized" in rendered_phone
    assert " is null" not in rendered_phone

    email_filters = _customer_contact_filters("Customer@Example.COM")
    rendered_email = _render(email_filters)
    assert "email_normalized" in rendered_email
    assert "customer@example.com" in rendered_email
    assert " is null" not in rendered_email


def test_duplicate_contact_filters_do_not_add_null_contact_match():
    filters = _ticket_duplicate_contact_filters("customer@example.com")
    rendered = _render(filters)
    assert "email_normalized" in rendered
    assert " is null" not in rendered


def test_duplicate_contact_filters_include_normalized_phone_when_distinct():
    filters = _ticket_duplicate_contact_filters("+41 79 123 45 67")
    rendered = _render(filters)
    assert "phone_normalized" in rendered
    assert " is null" not in rendered
