from __future__ import annotations

from app.services.email_inbound import record_inbound_email
from app.services.outbound_adapters.email import build_or_get_email_metadata
from email_test_utils import email_message, make_session, ticket, verified_email_account


def test_inbound_reply_links_only_by_deterministic_token(tmp_path):
    engine, db = make_session(tmp_path)
    try:
        ticket_row = ticket(db)
        verified_email_account(db)
        message = email_message(db, ticket_row)
        metadata = build_or_get_email_metadata(db, message=message, ticket=ticket_row, subject="Update", to_email="alice@example.test")
        inbound = record_inbound_email(db, {"messageId": "in-2", "from": "alice@example.test", "to": [f"support+nx-{metadata.reply_token}@example.test"], "subject": "Anything"})
        assert inbound.ticket_id == ticket_row.id
        assert inbound.link_status == "linked"
    finally:
        db.close()
        engine.dispose()
