from __future__ import annotations

from app.services.support_conversation_privacy import (
    mask_support_contact,
    mask_support_display_name,
    safe_support_message_preview,
    safe_support_tracking_reference,
)


def test_list_privacy_masks_name_contact_and_tracking() -> None:
    assert mask_support_display_name("Maxim Zhang") == "M•••"
    assert mask_support_display_name("") == "Customer"
    assert mask_support_contact("person@example.com") == "p***@example.com"
    assert mask_support_contact("+382 67 123 456") == "phone ending 56"
    assert safe_support_tracking_reference("CH020000129131") == "parcel ending 129131"


def test_message_preview_redacts_customer_identifiers() -> None:
    preview = safe_support_message_preview(
        "Call +382 67 123 456 or person@example.com about CH020000129131 at 12 Main Street."
    )
    assert preview is not None
    assert "+382" not in preview
    assert "person@example.com" not in preview
    assert "CH020000129131" not in preview
    assert "12 Main Street" not in preview
    assert "[redacted_phone]" in preview
    assert "[redacted_email]" in preview


def test_message_preview_is_bounded_and_handles_unsupported_values() -> None:
    assert safe_support_message_preview("x" * 500, limit=80) is not None
    assert len(safe_support_message_preview("x" * 500, limit=80) or "") <= 80
    assert safe_support_message_preview(None) is None
