from __future__ import annotations

from app.enums import MessageStatus, SourceChannel
from app.models import EmailDeliveryEvent, TicketOutboundMessage
from email_test_utils import make_session, ticket


def test_email_queue_and_event_counts_are_queryable(tmp_path):
    engine, db = make_session(tmp_path)
    try:
        ticket_row = ticket(db)
        db.add(TicketOutboundMessage(ticket_id=ticket_row.id, channel=SourceChannel.email, status=MessageStatus.pending, body="hello"))
        db.add(EmailDeliveryEvent(provider="ses", provider_event_id="evt-count", event_type="delivery", payload_json={}, occurred_at=ticket_row.created_at))
        db.flush()
        assert db.query(TicketOutboundMessage).filter(TicketOutboundMessage.channel == SourceChannel.email, TicketOutboundMessage.status == MessageStatus.pending).count() == 1
        assert db.query(EmailDeliveryEvent).filter(EmailDeliveryEvent.provider == "ses").count() == 1
    finally:
        db.close()
        engine.dispose()
