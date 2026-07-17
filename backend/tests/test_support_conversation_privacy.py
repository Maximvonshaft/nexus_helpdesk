from __future__ import annotations

from app.services.support_conversation_privacy import (
    mask_support_contact,
    mask_support_display_name,
    safe_support_tracking_reference,
)


def test_list_privacy_masks_name_contact_and_tracking() -> None:
    assert mask_support_display_name("Maxim Zhang") == "M•••"
    assert mask_support_display_name("") == "Customer"
    assert mask_support_contact("person@example.com") == "p***@example.com"
    assert mask_support_contact("+382 67 123 456") == "phone ending 56"
    assert safe_support_tracking_reference("CH020000129131") == "parcel ending 129131"
