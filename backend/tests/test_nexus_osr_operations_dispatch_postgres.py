from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Customer, Ticket
from app.models_operations_dispatch import OperationsDispatchOutboxRecord
from app.models_osr import WhatsAppRoutingRuleRecord
from app.services.nexus_osr.operations_dispatch_outbox import (
    OperationsDispatchLeaseLostError,
    build_operations_dispatch_key,
    claim_next_operations_dispatch,
    digest_identifier,
    enqueue_operations_dispatch,
    mark_operations_dispatch_success,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires PostgreSQL DATABASE_URL",
)

register_all_models()
NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
RAW_GROUP_ID = "120363012345678901@g.us"


@pytest.fixture()
def pg_database():
    engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    try:
        yield engine, Session
    finally:
        engine.dispose()


def _seed(Session, *, suffix: str):
    with Session() as db:
        customer = Customer(name=f"Dispatch {suffix}", external_ref=f"dispatch-{suffix}")
        db.add(customer)
        db.flush()
        ticket = Ticket(
            ticket_no=f"OSR-PG-{suffix[:20]}",
            title="PostgreSQL dispatch test",
            description="PostgreSQL dispatch test",
            customer_id=customer.id,
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.pending_assignment,
            conversation_state=ConversationState.ai_active,
            country_code="ME",
            case_type="delivery_delay",
        )
        rule = WhatsAppRoutingRuleRecord(
            country_code="ME",
            issue_type=f"delivery_{suffix[:24]}",
            channel="whatsapp",
            destination_group_id=RAW_GROUP_ID,
            priority=10,
            enabled=True,
        )
        db.add_all([ticket, rule])
        db.commit()
        return ticket.id, rule.id


def _kwargs(ticket_id: int, rule_id: int, *, suffix: str):
    return {
        "dispatch_key": build_operations_dispatch_key(
            tenant_key="tenant-pg",
            country_code="ME",
            channel_key="whatsapp",
            routing_rule_id=rule_id,
            ticket_id=ticket_id,
            case_reference=suffix,
        ),
        "tenant_key": "tenant-pg",
        "country_code": "ME",
        "channel_key": "whatsapp",
        "routing_rule_id": rule_id,
        "destination_group_key": "provider-group:postgres-safe",
        "destination_group_hash": digest_identifier(RAW_GROUP_ID),
        "ticket_id": ticket_id,
        "max_attempts": 4,
        "now": NOW,
    }


def test_postgres_concurrent_enqueue_resolves_to_one_record(pg_database):
    _, Session = pg_database
    suffix = uuid4().hex
    ticket_id, rule_id = _seed(Session, suffix=suffix)
    barrier = Barrier(2)

    def enqueue_once():
        with Session() as db:
            barrier.wait(timeout=15)
            result = enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix=suffix))
            db.commit()
            return result.record.id, result.created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enqueue_once(), range(2)))

    ids = {record_id for record_id, _ in results}
    assert len(ids) == 1
    assert sorted(created for _, created in results) == [False, True]
    with Session() as db:
        assert db.query(OperationsDispatchOutboxRecord).filter(
            OperationsDispatchOutboxRecord.dispatch_key == _kwargs(ticket_id, rule_id, suffix=suffix)["dispatch_key"]
        ).count() == 1


def test_postgres_skip_locked_claims_distinct_rows(pg_database):
    _, Session = pg_database
    suffix = uuid4().hex
    ticket_id, rule_id = _seed(Session, suffix=suffix)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix=f"{suffix}-a"))
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix=f"{suffix}-b"))
        db.commit()

    start = Barrier(2)
    claimed = Barrier(2)

    def claim_once(worker: str):
        with Session() as db:
            start.wait(timeout=15)
            row = claim_next_operations_dispatch(db, lease_owner=worker, now=NOW, lease_seconds=60)
            assert row is not None
            row_id = row.id
            claimed.wait(timeout=15)
            db.commit()
            return row_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        record_ids = list(pool.map(claim_once, ["worker-a", "worker-b"]))

    assert len(set(record_ids)) == 2


def test_postgres_single_row_contention_has_one_owner(pg_database):
    _, Session = pg_database
    suffix = uuid4().hex
    ticket_id, rule_id = _seed(Session, suffix=suffix)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix=f"{suffix}-single"))
        db.commit()

    start = Barrier(2)
    finish = Barrier(2)

    def claim_once(worker: str):
        with Session() as db:
            start.wait(timeout=15)
            row = claim_next_operations_dispatch(db, lease_owner=worker, now=NOW, lease_seconds=60)
            result = None if row is None else (row.id, row.lease_owner)
            finish.wait(timeout=15)
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim_once, ["worker-one", "worker-two"]))

    owners = [item for item in results if item is not None]
    assert len(owners) == 1
    assert owners[0][1] in {"worker-one", "worker-two"}


def test_postgres_expired_lease_recovery_rejects_stale_owner(pg_database):
    _, Session = pg_database
    suffix = uuid4().hex
    ticket_id, rule_id = _seed(Session, suffix=suffix)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix=f"{suffix}-recovery"))
        db.commit()
        first = claim_next_operations_dispatch(db, lease_owner="worker-old", now=NOW, lease_seconds=10)
        record_id = first.id
        db.commit()

    with Session() as db:
        recovered = claim_next_operations_dispatch(
            db,
            lease_owner="worker-new",
            now=NOW + timedelta(seconds=11),
            lease_seconds=30,
        )
        assert recovered is not None
        assert recovered.id == record_id
        assert recovered.lease_owner == "worker-new"
        assert recovered.attempt_count == 2
        db.commit()

    with Session() as db:
        with pytest.raises(OperationsDispatchLeaseLostError):
            mark_operations_dispatch_success(
                db,
                record_id=record_id,
                lease_owner="worker-old",
                now=NOW + timedelta(seconds=12),
            )
        db.rollback()

    with Session() as db:
        current = db.get(OperationsDispatchOutboxRecord, record_id)
        assert current.status == "processing"
        assert current.lease_owner == "worker-new"
