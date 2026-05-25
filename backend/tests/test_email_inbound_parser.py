from __future__ import annotations

from app.services.email_inbound import extract_reply_token, record_inbound_email
from email_test_utils import make_session


def test_extract_reply_token_from_plus_address():
    assert extract_reply_token(to_email="support+nx-abcdef1234567890@example.test") == "abcdef1234567890"


def test_subject_only_inbound_goes_to_manual_review(tmp_path):
    engine, db = make_session(tmp_path)
    try:
        inbound = record_inbound_email(db, {"messageId": "in-1", "from": "alice@example.test", "to": ["support@example.test"], "subject": "Re: existing case"})
        assert inbound.ticket_id is None
        assert inbound.link_status == "manual_review"
    finally:
        db.close()
        engine.dispose()
