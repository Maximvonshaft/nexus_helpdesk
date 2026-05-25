from __future__ import annotations

import pytest

from app.models import EmailSuppression
from app.services.email_security import is_email_suppressed, reject_header_injection, sanitize_email_body
from email_test_utils import make_session


def test_header_injection_is_rejected():
    with pytest.raises(ValueError, match="email_header_injection"):
        reject_header_injection("Hello\r\nBcc: attacker@example.test")


def test_html_script_is_removed_and_suppression_is_checked(tmp_path):
    assert "<script" not in sanitize_email_body("<p>ok</p><script>alert(1)</script>").lower()
    engine, db = make_session(tmp_path)
    try:
        db.add(EmailSuppression(email="Alice@Example.Test", email_normalized="alice@example.test", reason="bounce", is_active=True))
        db.flush()
        assert is_email_suppressed(db, "alice@example.test") is True
    finally:
        db.close()
        engine.dispose()
