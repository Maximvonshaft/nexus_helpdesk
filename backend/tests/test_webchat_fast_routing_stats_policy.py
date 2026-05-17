from __future__ import annotations

from sqlalchemy import delete

from app.api.stats import webchat_fast_stats
from app.db import Base, SessionLocal, engine
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.models import ChannelAccount, Customer, Market, Ticket
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_fast_session_service import (
    extract_fast_business_state,
    get_or_create_fast_conversation,
    get_or_create_fast_ticket,
    resolve_fast_routing_context,
)
from app.services.webchat_handoff_policy import decide_server_handoff_policy
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatMessage


def _reset_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for model in (
            WebchatFastIdempotency,
            WebchatMessage,
            WebchatConversation,
            Ticket,
            Customer,
            ChannelAccount,
            Market,
        ):
            db.execute(delete(model))
        db.commit()
    finally:
        db.close()


def setup_function() -> None:
    _reset_db()


def test_fast_handoff_ticket_persists_resolved_market_and_channel_account() -> None:
    db = SessionLocal()
    try:
        market = Market(code="CH", name="Switzerland", country_code="CH", is_active=True)
        db.add(market)
        db.flush()
        account = ChannelAccount(
            provider="openclaw",
            account_id="speedaf-ch-webchat",
            display_name="Speedaf CH WebChat",
            market_id=market.id,
            is_active=True,
            priority=10,
        )
        db.add(account)
        db.flush()

        conversation = get_or_create_fast_conversation(
            db,
            tenant_key="speedaf",
            channel_key="website",
            session_id="session-routing-1",
        )
        state = extract_fast_business_state(
            body="I want a refund for parcel SF123456789CH",
            context=[],
            session_id="session-routing-1",
        )
        routing = resolve_fast_routing_context(
            db,
            market_code="CH",
            channel_account_key="speedaf-ch-webchat",
        )

        ticket = get_or_create_fast_ticket(
            db,
            conversation=conversation,
            business_state=state,
            handoff_reason="test_handoff",
            recommended_agent_action="Review this test request",
            customer_message="I want a refund for parcel SF123456789CH",
            routing_context=routing,
        )

        assert ticket.market_id == market.id
        assert ticket.country_code == "CH"
        assert ticket.channel_account_id == account.id
        assert conversation.ticket_id == ticket.id
    finally:
        db.close()


def test_configured_handoff_rule_takes_priority_over_builtin_rules() -> None:
    decision = decide_server_handoff_policy(
        body="Please handle the blue zebra case.",
        recent_context=[],
        configured_rules=[
            {
                "rule_id": "custom_blue_zebra",
                "phrases": ["blue zebra"],
                "handoff_reason": "custom_blue_zebra_requires_review",
                "recommended_agent_action": "Apply the configured custom SOP.",
                "customer_reply": "A specialist will review this custom case.",
            }
        ],
    )

    assert decision.handoff_required is True
    assert decision.rule_id == "custom_blue_zebra"
    assert decision.handoff_reason == "custom_blue_zebra_requires_review"
    assert "custom_blue_zebra" in (decision.recommended_agent_action or "")
    assert decision.customer_reply == "A specialist will review this custom case."


def test_webchat_fast_stats_exposes_ticketless_and_handoff_operational_counters() -> None:
    db = SessionLocal()
    now = utc_now()
    try:
        ticketless = WebchatConversation(
            public_id="wcf_ticketless",
            visitor_token_hash="hash-ticketless",
            tenant_key="speedaf",
            channel_key="website",
            visitor_ref="session-ticketless",
            origin="webchat-fast",
            status="open",
            fast_session_id="session-ticketless",
            last_intent="tracking_lookup",
            created_at=now,
            updated_at=now,
            last_seen_at=now,
            fast_context_updated_at=now,
        )
        db.add(ticketless)
        db.flush()
        db.add(WebchatMessage(
            conversation_id=ticketless.id,
            direction="visitor",
            body="Where is SF123456789CH?",
            body_text="Where is SF123456789CH?",
            message_type="text",
            client_message_id="m1",
            delivery_status="sent",
            author_label="Customer",
            created_at=now,
        ))
        db.add(WebchatMessage(
            conversation_id=ticketless.id,
            direction="ai",
            body="Please share more details.",
            body_text="Please share more details.",
            message_type="text",
            client_message_id="m1:ai",
            delivery_status="sent",
            author_label="Speedy",
            created_at=now,
        ))

        customer = Customer(name="Webchat Visitor", external_ref="session-handoff")
        db.add(customer)
        db.flush()
        ticket = Ticket(
            ticket_no="CS-TEST-FAST-STATS",
            title="WebChat handoff",
            description="handoff",
            customer_id=customer.id,
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.pending_assignment,
            source_chat_id="webchat-fast:wcf_handoff",
        )
        db.add(ticket)
        db.flush()
        handoff = WebchatConversation(
            public_id="wcf_handoff",
            visitor_token_hash="hash-handoff",
            tenant_key="speedaf",
            channel_key="website",
            ticket_id=ticket.id,
            visitor_ref="session-handoff",
            origin="webchat-fast",
            status="open",
            fast_session_id="session-handoff",
            last_intent="handoff",
            created_at=now,
            updated_at=now,
            last_seen_at=now,
            fast_context_updated_at=now,
        )
        db.add(handoff)
        db.flush()
        db.add(WebchatMessage(
            conversation_id=handoff.id,
            ticket_id=ticket.id,
            direction="system",
            body="WebChat Fast Lane created a human-review handoff ticket.",
            body_text="WebChat Fast Lane created a human-review handoff ticket.",
            message_type="text",
            client_message_id="m2:handoff",
            delivery_status="sent",
            author_label="System",
            created_at=now,
        ))
        db.add(WebchatFastIdempotency(
            tenant_key="speedaf",
            session_id="session-ticketless",
            client_message_id="m1",
            request_hash="hash1",
            status="done",
            response_json={"ok": True},
            created_at=now,
            updated_at=now,
            expires_at=now,
        ))
        db.add(WebchatFastIdempotency(
            tenant_key="speedaf",
            session_id="session-error",
            client_message_id="m3",
            request_hash="hash3",
            status="failed",
            error_code="ai_invalid_output",
            created_at=now,
            updated_at=now,
            expires_at=now,
        ))
        db.commit()

        stats = webchat_fast_stats(days=7, db=db, current_user=object())

        assert stats["total_sessions"] == 2
        assert stats["ticketless_sessions"] == 1
        assert stats["ai_resolved_sessions"] == 1
        assert stats["handoff_sessions"] == 1
        assert stats["tickets_created"] == 1
        assert stats["idempotency_by_status"]["done"] == 1
        assert stats["errors_by_code"]["ai_invalid_output"] == 1
        assert stats["sessions_by_intent"]["tracking_lookup"] == 1
    finally:
        db.close()
