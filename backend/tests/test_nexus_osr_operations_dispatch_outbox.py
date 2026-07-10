from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_dispatch_outbox_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, models_operations_dispatch, models_osr, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.models_operations_dispatch import OperationsDispatchOutboxRecord  # noqa: E402
from app.models_osr import WhatsAppRoutingRuleRecord  # noqa: E402
from app.services.nexus_osr.operations_dispatch_outbox import (  # noqa: E402
    DispatchLeaseLostError,
    OperationsDispatchStatus,
    build_dispatch_key,
    claim_next_operations_dispatch,
    controlled_backoff_seconds,
    enqueue_operations_dispatch,
    mark_operations_dispatch_failed,
    mark_operations_dispatch_succeeded,
    sha256_digest,
)
from app.services.nexus_osr.operations_dispatch_processor import (  # noqa: E402
    OperationsDispatchAdapterResult,
    OperationsDispatchRequest,
    process_next_operations_dispatch,
)


NOW = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
RAW_GROUP_ID = "120363012345678901@g.us"
RAW_EMAIL = "customer@example.com"
RAW_PHONE = "+382 67 123 456"
RAW_TRACKING = "ME020000362343"
RAW_ADDRESS = "address 123 Main Street Podgorica"


@pytest.fixture()
def database(tmp_path):
    db_file = tmp_path / "operations_dispatch.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    try:
        yield engine, Session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_scope(Session):
    with Session() as db:
        customer = Customer(name="Dispatch Test", external_ref="dispatch-test")
        db.add(customer)
        db.flush()
        ticket = Ticket(
            ticket_no="OSR-DISPATCH-000001",
            title="Dispatch test",
            description="Dispatch test",
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
            issue_type="delivery_delay",
            channel="whatsapp",
            destination_group_id=RAW_GROUP_ID,
            priority=10,
            enabled=True,
        )
        db.add_all([ticket, rule])
        db.commit()
        return ticket.id, rule.id


def _kwargs(ticket_id: int, rule_id: int, *, key: str = "dispatch-1", max_attempts: int = 3):
    return {
        "dispatch_key": build_dispatch_key("test", key),
        "tenant_key": "tenant-me",
        "country_code": "ME",
        "channel_key": "whatsapp",
        "routing_rule_id": rule_id,
        "destination_group_key": "whatsapp:me:delivery_delay:destination",
        "destination_group_hash": sha256_digest(RAW_GROUP_ID),
        "ticket_id": ticket_id,
        "max_attempts": max_attempts,
        "now": NOW,
    }


def _dump(record: OperationsDispatchOutboxRecord) -> str:
    return json.dumps(
        {
            column.name: getattr(record, column.name)
            for column in OperationsDispatchOutboxRecord.__table__.columns
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def test_enqueue_is_idempotent_and_dispatch_key_is_database_unique(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        first = enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id))
        second = enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id))
        db.commit()

        assert first.created is True
        assert second.created is False
        assert first.record.id == second.record.id
        assert db.query(OperationsDispatchOutboxRecord).count() == 1

        duplicate = OperationsDispatchOutboxRecord(
            **{
                key: value
                for key, value in _kwargs(ticket_id, rule_id).items()
                if key not in {"now"}
            }
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_concurrent_enqueue_produces_one_record(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    barrier = Barrier(2)

    def enqueue_once():
        with Session() as db:
            barrier.wait(timeout=10)
            result = enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, key="concurrent"))
            db.commit()
            return result.record.id, result.created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enqueue_once(), range(2)))

    with Session() as db:
        rows = db.query(OperationsDispatchOutboxRecord).all()
        assert len(rows) == 1
        assert {item[0] for item in results} == {rows[0].id}
        assert sorted(item[1] for item in results) == [False, True]


def test_worker_lease_and_expiry_recovery(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, key="lease"))
        db.commit()

        first = claim_next_operations_dispatch(db, lease_owner="worker-a", now=NOW, lease_seconds=20)
        assert first is not None
        assert first.status == OperationsDispatchStatus.PROCESSING
        assert first.attempt_count == 1
        db.commit()

        assert claim_next_operations_dispatch(
            db,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=19),
            lease_seconds=20,
        ) is None
        db.commit()

        recovered = claim_next_operations_dispatch(
            db,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=21),
            lease_seconds=20,
        )
        assert recovered is not None
        assert recovered.id == first.id
        assert recovered.lease_owner == "worker-b"
        assert recovered.attempt_count == 2


def test_retry_backoff_is_bounded_and_max_attempts_dead_letter(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, key="retry", max_attempts=2))
        db.commit()

        first = claim_next_operations_dispatch(db, lease_owner="worker-a", now=NOW, lease_seconds=60)
        assert first is not None
        failed = mark_operations_dispatch_failed(
            db,
            record_id=first.id,
            lease_owner="worker-a",
            retryable=True,
            error_category="timeout",
            error_summary="temporary timeout",
            now=NOW + timedelta(seconds=1),
            backoff_base_seconds=30,
            backoff_max_seconds=60,
        )
        assert failed.status == OperationsDispatchStatus.RETRYABLE
        assert failed.next_retry_at is not None
        assert failed.next_retry_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=31)
        db.commit()

        assert claim_next_operations_dispatch(
            db,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=30),
        ) is None
        db.commit()

        second = claim_next_operations_dispatch(
            db,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=31),
        )
        assert second is not None
        assert second.attempt_count == 2
        dead = mark_operations_dispatch_failed(
            db,
            record_id=second.id,
            lease_owner="worker-b",
            retryable=True,
            error_category="timeout",
            error_summary="still unavailable",
            now=NOW + timedelta(seconds=32),
        )
        assert dead.status == OperationsDispatchStatus.DEAD_LETTER
        assert dead.next_retry_at is None

    assert controlled_backoff_seconds(attempt_count=1, base_seconds=30, max_seconds=60) == 30
    assert controlled_backoff_seconds(attempt_count=2, base_seconds=30, max_seconds=60) == 60
    assert controlled_backoff_seconds(attempt_count=20, base_seconds=30, max_seconds=60) == 60


def test_stale_worker_cannot_ack_after_lease_expiry(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, key="stale"))
        db.commit()
        record = claim_next_operations_dispatch(db, lease_owner="worker-a", now=NOW, lease_seconds=10)
        assert record is not None
        db.commit()

        with pytest.raises(DispatchLeaseLostError):
            mark_operations_dispatch_succeeded(
                db,
                record_id=record.id,
                lease_owner="worker-a",
                now=NOW + timedelta(seconds=11),
            )


def test_processor_crash_recovery_and_duplicate_delivery_idempotency_key(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, key="crash"))
        db.commit()
        claimed = claim_next_operations_dispatch(db, lease_owner="crashed-worker", now=NOW, lease_seconds=10)
        assert claimed is not None
        db.commit()

        side_effect_keys: set[str] = set()
        adapter_calls: list[str] = []

        class _IdempotentAdapter:
            def dispatch(self, request: OperationsDispatchRequest) -> OperationsDispatchAdapterResult:
                adapter_calls.append(request.dispatch_key)
                duplicate = request.dispatch_key in side_effect_keys
                side_effect_keys.add(request.dispatch_key)
                return OperationsDispatchAdapterResult(
                    dispatched=True,
                    provider_acknowledgement={"status": "accepted", "duplicate": duplicate},
                    external_reference="provider-message-raw-1",
                )

        adapter = _IdempotentAdapter()
        # Simulate a provider side effect followed by a worker crash before ack persistence.
        first_request = OperationsDispatchRequest(
            outbox_id=claimed.id,
            dispatch_key=claimed.dispatch_key,
            tenant_key=claimed.tenant_key,
            country_code=claimed.country_code,
            channel_key=claimed.channel_key,
            routing_rule_id=claimed.routing_rule_id,
            destination_group_key=claimed.destination_group_key,
            destination_group_hash=claimed.destination_group_hash,
            attempt_count=claimed.attempt_count,
        )
        adapter.dispatch(first_request)

        recovered = process_next_operations_dispatch(
            db,
            adapter=adapter,
            lease_owner="recovery-worker",
            now=NOW + timedelta(seconds=11),
            lease_seconds=30,
        )
        assert recovered is not None
        assert recovered.status == OperationsDispatchStatus.DISPATCHED
        assert len(side_effect_keys) == 1
        assert adapter_calls == [claimed.dispatch_key, claimed.dispatch_key]
        assert "\"duplicate\":true" in (recovered.provider_acknowledgement or "")

        assert process_next_operations_dispatch(
            db,
            adapter=adapter,
            lease_owner="third-worker",
            now=NOW + timedelta(seconds=12),
        ) is None
        assert len(adapter_calls) == 2


def test_provider_ack_error_and_external_reference_are_redacted(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, key="redaction"))
        db.commit()
        record = claim_next_operations_dispatch(db, lease_owner="worker-a", now=NOW, lease_seconds=60)
        assert record is not None
        succeeded = mark_operations_dispatch_succeeded(
            db,
            record_id=record.id,
            lease_owner="worker-a",
            provider_acknowledgement={
                "status": "accepted",
                "provider_group_id": RAW_GROUP_ID,
                "detail": f"{RAW_EMAIL} {RAW_PHONE} {RAW_TRACKING} {RAW_ADDRESS}",
            },
            external_reference="provider-message-raw-123",
            now=NOW + timedelta(seconds=1),
        )
        dumped = _dump(succeeded)
        for raw in [
            RAW_GROUP_ID,
            RAW_EMAIL,
            RAW_PHONE,
            RAW_TRACKING,
            "123 Main Street",
            "provider-message-raw-123",
        ]:
            assert raw not in dumped
        assert succeeded.external_reference_safe and succeeded.external_reference_safe.startswith("sha256:")

        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, key="redaction-error"))
        db.commit()
        second = claim_next_operations_dispatch(db, lease_owner="worker-b", now=NOW + timedelta(seconds=2))
        assert second is not None
        failed = mark_operations_dispatch_failed(
            db,
            record_id=second.id,
            lease_owner="worker-b",
            retryable=False,
            error_category="Provider Group Failure!",
            error_summary=f"group_id={RAW_GROUP_ID}; {RAW_EMAIL}; {RAW_PHONE}; {RAW_TRACKING}; {RAW_ADDRESS}",
            now=NOW + timedelta(seconds=3),
        )
        dumped = _dump(failed)
        for raw in [RAW_GROUP_ID, RAW_EMAIL, RAW_PHONE, RAW_TRACKING, "123 Main Street"]:
            assert raw not in dumped
        assert failed.error_category == "redacted_error_category"
