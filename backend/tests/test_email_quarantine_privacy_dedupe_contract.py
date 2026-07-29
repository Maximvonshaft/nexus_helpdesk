from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_privacy_erasure_preserves_email_quarantine_dedupe_authority() -> None:
    authority = json.loads(
        (ROOT / "config/privacy/data-field-authority.v1.json").read_text(
            encoding="utf-8"
        )
    )["resources"]["email_intake_quarantine"]
    assert authority["operational_dedupe_keys"] == [
        "provider_message_id",
        "mailbox_uid",
    ]
    assert "provider_message_id" not in authority["pii"]
    assert "mailbox_uid" not in authority["pii"]

    lifecycle = (
        ROOT / "backend/app/services/channel_identity_privacy_lifecycle.py"
    ).read_text(encoding="utf-8")
    assert "row.provider_message_id =" not in lifecycle
    assert "row.mailbox_uid =" not in lifecycle
    assert "row.from_address =" in lifecycle
    assert 'row.body = "[redacted by privacy request]"' in lifecycle
