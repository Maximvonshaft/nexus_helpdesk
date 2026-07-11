from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/server_fact_evidence_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, models_osr, webchat_models  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.models_osr import CaseContextRecord  # noqa: E402
from app.services.server_fact_evidence import MAX_SERVER_FACT_AGE, resolve_server_fact_evidence  # noqa: E402
from app.services.tracking_fact_schema import (  # noqa: E402
    EVIDENCE_AVAILABLE,
    FRESHNESS_FRESH,
    SOURCE_AUTHORITY_PRIMARY,
    hash_tracking_number,
)
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "server_fact_evidence.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _case(db, *, tenant: str = "tenant-a", tracking: str = "CH020000129131"):
    customer = Customer(name="Evidence Visitor", external_ref=f"evidence-{tenant}-{tracking}")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"EVID-{customer.id}",
        title="Evidence case",
        description="Evidence case",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.human_owned,
        tracking_number=tracking,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="webchat",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"evidence_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key=tenant,
        channel_key="webchat",
        ticket_id=ticket.id,
        status="open",
        last_tracking_number=tracking,
    )
    db.add(conversation)
    db.flush()
    return ticket, conversation


def _fact(*, tracking: str = "CH020000129131", now=None, **overrides):
    current = now or utc_now()
    payload = {
        "fact_evidence_present": True,
        "fact_source": "speedaf_api.tracking_lookup",
        "tool_name": "speedaf.order.query",
        "tool_status": "success",
        "pii_redacted": True,
        "checked_at": current.isoformat(),
        "observed_at": current.isoformat(),
        "source_authority": SOURCE_AUTHORITY_PRIMARY,
        "evidence_state": EVIDENCE_AVAILABLE,
        "freshness": FRESHNESS_FRESH,
        "contradictions": [],
        "tracking_number_hash": hash_tracking_number(tracking),
        "safe_tracking_reference": f"parcel ending {tracking[-6:]}",
    }
    payload.update(overrides)
    return payload


def _context(db, ticket, conversation, *, fact=None, tenant: str | None = None, active: bool = True, expires_at=None):
    row = CaseContextRecord(
        tenant_id=tenant or conversation.tenant_key,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        channel="webchat",
        status="active" if active else "closed",
        is_active=active,
        safe_tracking_reference=f"parcel ending {ticket.tracking_number[-6:]}",
        tracking_number_hash=hash_tracking_number(ticket.tracking_number),
        last_mcp_fact_json=fact,
        expires_at=expires_at,
        closed_at=None if active else utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def test_accepts_only_current_primary_server_fact(db_session):
    now = utc_now()
    ticket, conversation = _case(db_session)
    row = _context(db_session, ticket, conversation, fact=_fact(now=now), expires_at=now + timedelta(hours=1))

    decision = resolve_server_fact_evidence(
        db_session,
        ticket=ticket,
        conversation=conversation,
        now=now,
    )

    assert decision.present is True
    assert decision.reason == "trusted_server_fact_available"
    assert decision.reference_id == row.id
    assert decision.authority == SOURCE_AUTHORITY_PRIMARY
    assert decision.evidence_state == EVIDENCE_AVAILABLE
    assert decision.tracking_number_hash == hash_tracking_number(ticket.tracking_number)
    assert ticket.tracking_number not in str(decision.audit_payload())


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"fact_evidence_present": False}, "trusted_fact_not_present"),
        ({"pii_redacted": False}, "evidence_not_pii_redacted"),
        ({"source_authority": "history_enrichment"}, "evidence_not_primary_authority"),
        ({"evidence_state": "stale"}, "evidence_state_not_available"),
        ({"freshness": "stale"}, "evidence_not_fresh"),
        ({"tool_status": "error"}, "evidence_tool_not_successful"),
        ({"contradictions": [{"kind": "status_conflict"}]}, "evidence_has_contradictions"),
        ({"tracking_number_hash": hash_tracking_number("CH999999999999")}, "evidence_context_tracking_mismatch"),
    ],
)
def test_rejects_untrusted_fact_states(db_session, override, reason):
    now = utc_now()
    ticket, conversation = _case(db_session)
    _context(db_session, ticket, conversation, fact=_fact(now=now, **override), expires_at=now + timedelta(hours=1))

    decision = resolve_server_fact_evidence(db_session, ticket=ticket, conversation=conversation, now=now)

    assert decision.present is False
    assert decision.reason == reason


def test_rejects_old_future_expired_and_inactive_evidence(db_session):
    now = utc_now()
    ticket, conversation = _case(db_session)
    old = _context(
        db_session,
        ticket,
        conversation,
        fact=_fact(now=now - MAX_SERVER_FACT_AGE - timedelta(seconds=1)),
        expires_at=now + timedelta(hours=1),
    )
    assert resolve_server_fact_evidence(db_session, ticket=ticket, conversation=conversation, evidence_reference_id=old.id, now=now).reason == "evidence_checked_at_too_old"

    old.is_active = False
    old.status = "closed"
    old.closed_at = now
    db_session.flush()
    assert resolve_server_fact_evidence(db_session, ticket=ticket, conversation=conversation, evidence_reference_id=old.id, now=now).reason == "evidence_context_inactive"

    ticket2, conversation2 = _case(db_session, tenant="tenant-b", tracking="CH020000129132")
    expired = _context(
        db_session,
        ticket2,
        conversation2,
        fact=_fact(tracking=ticket2.tracking_number, now=now),
        expires_at=now - timedelta(seconds=1),
    )
    assert resolve_server_fact_evidence(db_session, ticket=ticket2, conversation=conversation2, evidence_reference_id=expired.id, now=now).reason == "evidence_context_expired"

    ticket3, conversation3 = _case(db_session, tenant="tenant-c", tracking="CH020000129133")
    future = _context(
        db_session,
        ticket3,
        conversation3,
        fact=_fact(tracking=ticket3.tracking_number, now=now + timedelta(minutes=6)),
        expires_at=now + timedelta(hours=1),
    )
    assert resolve_server_fact_evidence(db_session, ticket=ticket3, conversation=conversation3, evidence_reference_id=future.id, now=now).reason == "evidence_checked_at_in_future"


def test_explicit_reference_cannot_cross_tenant_or_case(db_session):
    now = utc_now()
    ticket_a, conversation_a = _case(db_session, tenant="tenant-a", tracking="CH020000129141")
    ticket_b, conversation_b = _case(db_session, tenant="tenant-b", tracking="CH020000129142")
    row_b = _context(
        db_session,
        ticket_b,
        conversation_b,
        fact=_fact(tracking=ticket_b.tracking_number, now=now),
        expires_at=now + timedelta(hours=1),
    )

    decision = resolve_server_fact_evidence(
        db_session,
        ticket=ticket_a,
        conversation=conversation_a,
        evidence_reference_id=row_b.id,
        now=now,
    )

    assert decision.present is False
    assert decision.reason == "evidence_not_found_or_out_of_scope"
    assert decision.reference_id is None


def test_auto_resolution_does_not_use_expired_context(db_session):
    now = utc_now()
    ticket, conversation = _case(db_session)
    _context(
        db_session,
        ticket,
        conversation,
        fact=_fact(now=now),
        expires_at=now - timedelta(seconds=1),
    )

    decision = resolve_server_fact_evidence(db_session, ticket=ticket, conversation=conversation, now=now)

    assert decision.present is False
    assert decision.reason == "evidence_context_expired"
