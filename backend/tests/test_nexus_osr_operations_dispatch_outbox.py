from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_dispatch_outbox_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app.db import Base
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Customer, Ticket, TicketEvent
from app.models_operations_dispatch import OperationsDispatchOutboxRecord
from app.models_osr import WhatsAppRoutingRuleRecord
from app.services.nexus_osr.operations_dispatch_outbox import (
    OperationsDispatchCollisionError,
    OperationsDispatchLeaseLostError,
    OperationsDispatchStatus,
    build_operations_dispatch_key,
    claim_next_operations_dispatch,
    digest_identifier,
    enqueue_operations_dispatch,
    mark_operations_dispatch_failure,
    mark_operations_dispatch_success,
)
from app.services.nexus_osr.operations_dispatch_processor import (
    DisabledOperationsDispatchAdapter,
    OperationsDispatchAdapterResult,
    OperationsDispatchEnvelope,
    process_operations_dispatch_batch,
)

register_all_models()

NOW = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
RAW_GROUP_ID = "120363012345678901@g.us"
RAW_EMAIL = "customer@example.com"
RAW_PHONE = "+382 67 123 456"
RAW_TRACKING = "ME020000362343"
RAW_ADDRESS = "123 Main Street Podgorica"
RAW_SECRET = "sk-proj-OUTBOXSECRET123456789"


@pytest.fixture()
def database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'operations-dispatch.db'}",
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


def _kwargs(ticket_id: int, rule_id: int, *, suffix: str = "default", max_attempts: int = 3):
    return {
        "dispatch_key": build_operations_dispatch_key(
            tenant_key="tenant-me",
            country_code="ME",
            channel_key="whatsapp",
            routing_rule_id=rule_id,
            ticket_id=ticket_id,
            case_reference=suffix,
        ),
        "tenant_key": "tenant-me",
        "country_code": "ME",
        "channel_key": "whatsapp",
        "routing_rule_id": rule_id,
        "destination_group_key": "provider-group:business-key",
        "destination_group_hash": digest_identifier(RAW_GROUP_ID),
        "ticket_id": ticket_id,
        "max_attempts": max_attempts,
        "now": NOW,
    }


def _enqueue_and_claim(Session, *, suffix: str, lease_seconds: int = 20):
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix=suffix))
        db.commit()
        claimed = claim_next_operations_dispatch(
            db,
            lease_owner="worker-a",
            now=NOW,
            lease_seconds=lease_seconds,
        )
        assert claimed is not None
        record_id = claimed.id
        db.commit()
    return ticket_id, rule_id, record_id


def _dump(record: OperationsDispatchOutboxRecord) -> str:
    return json.dumps(
        {column.name: getattr(record, column.name) for column in OperationsDispatchOutboxRecord.__table__.columns},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def test_enqueue_is_idempotent_database_unique_and_scope_safe(database):
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

        collision = dict(_kwargs(ticket_id, rule_id))
        collision["tenant_key"] = "tenant-other"
        with pytest.raises(OperationsDispatchCollisionError):
            enqueue_operations_dispatch(db, **collision)

        direct_duplicate = OperationsDispatchOutboxRecord(
            dispatch_key=first.record.dispatch_key,
            tenant_key="tenant-me",
            country_code="ME",
            channel_key="whatsapp",
            routing_rule_id=rule_id,
            destination_group_key="provider-group:business-key",
            destination_group_hash=digest_identifier(RAW_GROUP_ID),
            ticket_id=ticket_id,
            max_attempts=3,
            status="pending",
            attempt_count=0,
            created_at=NOW,
            updated_at=NOW,
        )
        db.add(direct_duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_worker_lease_blocks_second_worker_before_expiry(database):
    _, Session = database
    _, _, record_id = _enqueue_and_claim(Session, suffix="lease-block")

    with Session() as db:
        assert claim_next_operations_dispatch(
            db,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=19),
            lease_seconds=20,
        ) is None
        current = db.get(OperationsDispatchOutboxRecord, record_id)
        assert current.status == OperationsDispatchStatus.PROCESSING.value
        assert current.lease_owner == "worker-a"
        assert current.attempt_count == 1


def test_stale_owner_cannot_ack_after_lease_expiry(database):
    _, Session = database
    _, _, record_id = _enqueue_and_claim(Session, suffix="lease-stale")

    with Session() as db:
        with pytest.raises(OperationsDispatchLeaseLostError):
            mark_operations_dispatch_success(
                db,
                record_id=record_id,
                lease_owner="worker-a",
                now=NOW + timedelta(seconds=21),
            )
        db.rollback()
        current = db.get(OperationsDispatchOutboxRecord, record_id)
        assert current.status == OperationsDispatchStatus.PROCESSING.value
        assert current.lease_owner == "worker-a"
        assert current.dispatched_at is None


def test_expired_lease_is_recovered_by_new_owner(database):
    _, Session = database
    _, _, record_id = _enqueue_and_claim(Session, suffix="lease-recover")

    with Session() as db:
        recovered = claim_next_operations_dispatch(
            db,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=21),
            lease_seconds=20,
        )
        assert recovered is not None
        assert recovered.id == record_id
        assert recovered.status == OperationsDispatchStatus.PROCESSING.value
        assert recovered.lease_owner == "worker-b"
        assert recovered.attempt_count == 2


def test_retry_backoff_and_max_attempts_dead_letter(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix="retry", max_attempts=2))
        db.commit()

        first = claim_next_operations_dispatch(db, lease_owner="worker-a", now=NOW, lease_seconds=60)
        assert first is not None
        failed = mark_operations_dispatch_failure(
            db,
            record_id=first.id,
            lease_owner="worker-a",
            retryable=True,
            error_category="timeout",
            error_summary="temporary timeout",
            now=NOW + timedelta(seconds=1),
        )
        assert failed.status == OperationsDispatchStatus.RETRYABLE.value
        assert failed.next_retry_at == NOW + timedelta(seconds=31)
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
        assert second is not None and second.attempt_count == 2
        dead = mark_operations_dispatch_failure(
            db,
            record_id=second.id,
            lease_owner="worker-b",
            retryable=True,
            error_category="timeout",
            error_summary="still unavailable",
            now=NOW + timedelta(seconds=32),
        )
        assert dead.status == OperationsDispatchStatus.DEAD_LETTER.value
        assert dead.next_retry_at is None
        db.commit()

        assert claim_next_operations_dispatch(
            db,
            lease_owner="worker-c",
            now=NOW + timedelta(hours=2),
        ) is None


def test_provider_ack_external_reference_and_errors_are_redacted(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix="success-redaction"))
        db.commit()
        record = claim_next_operations_dispatch(db, lease_owner="worker-a", now=NOW, lease_seconds=60)
        assert record is not None
        succeeded = mark_operations_dispatch_success(
            db,
            record_id=record.id,
            lease_owner="worker-a",
            provider_acknowledgement={
                "status": "accepted",
                "provider_group_id": RAW_GROUP_ID,
                "detail": f"{RAW_EMAIL} {RAW_PHONE} {RAW_TRACKING} {RAW_ADDRESS} {RAW_SECRET}",
            },
            external_reference="provider-message-raw-123",
            now=NOW + timedelta(seconds=1),
        )
        dumped = _dump(succeeded)
        for raw in (RAW_GROUP_ID, RAW_EMAIL, RAW_PHONE, RAW_TRACKING, RAW_ADDRESS, RAW_SECRET, "provider-message-raw-123"):
            assert raw not in dumped
        assert succeeded.provider_acknowledgement.startswith("ack:sha256:")
        assert succeeded.external_reference_safe.startswith("external:sha256:")

        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix="failure-redaction"))
        db.commit()
        second = claim_next_operations_dispatch(db, lease_owner="worker-b", now=NOW + timedelta(seconds=2))
        assert second is not None
        failed = mark_operations_dispatch_failure(
            db,
            record_id=second.id,
            lease_owner="worker-b",
            retryable=False,
            error_category=f"provider {RAW_EMAIL}",
            error_summary=f"group={RAW_GROUP_ID}; {RAW_EMAIL}; {RAW_PHONE}; {RAW_TRACKING}; {RAW_ADDRESS}; {RAW_SECRET}",
            now=NOW + timedelta(seconds=3),
        )
        dumped = _dump(failed)
        for raw in (RAW_GROUP_ID, RAW_EMAIL, RAW_PHONE, RAW_TRACKING, RAW_ADDRESS, RAW_SECRET):
            assert raw not in dumped
        assert failed.error_category == "redacted_error_category"
        assert "[redacted_email]" in failed.error_summary_redacted


def test_processor_commits_claim_before_adapter_and_exposes_only_safe_envelope(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix="processor"))
        db.commit()

        observed: list[OperationsDispatchEnvelope] = []

        class Adapter:
            def dispatch(self, envelope: OperationsDispatchEnvelope) -> OperationsDispatchAdapterResult:
                observed.append(envelope)
                with Session() as other:
                    visible = other.get(OperationsDispatchOutboxRecord, envelope.outbox_id)
                    assert visible.status == OperationsDispatchStatus.PROCESSING.value
                    assert visible.lease_owner == "worker-a"
                serialized = json.dumps(envelope.__dict__, sort_keys=True, default=str)
                for raw in (RAW_GROUP_ID, RAW_EMAIL, RAW_PHONE, RAW_TRACKING, RAW_ADDRESS, RAW_SECRET):
                    assert raw not in serialized
                return OperationsDispatchAdapterResult(
                    success=True,
                    acknowledgement={"accepted": True, "group": RAW_GROUP_ID},
                    external_reference="provider-raw-reference",
                )

        assert process_operations_dispatch_batch(
            db,
            adapter=Adapter(),
            worker_id="worker-a",
            batch_size=1,
            lease_seconds=60,
        ) == 1

        record = db.query(OperationsDispatchOutboxRecord).one()
        assert record.status == OperationsDispatchStatus.DISPATCHED.value
        assert record.provider_acknowledgement.startswith("ack:sha256:")
        assert len(observed) == 1

        events = db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.id).all()
        assert len(events) == 2
        payloads = [json.loads(event.payload_json) for event in events]
        assert [payload["phase"] for payload in payloads] == ["claimed", "dispatched"]
        for payload in payloads:
            serialized = json.dumps(payload, sort_keys=True)
            for raw in (RAW_GROUP_ID, RAW_EMAIL, RAW_PHONE, RAW_TRACKING, RAW_ADDRESS, RAW_SECRET):
                assert raw not in serialized


def test_disabled_adapter_fails_closed_without_external_transport(database):
    _, Session = database
    ticket_id, rule_id = _seed_scope(Session)
    with Session() as db:
        enqueue_operations_dispatch(db, **_kwargs(ticket_id, rule_id, suffix="disabled"))
        db.commit()

        assert process_operations_dispatch_batch(
            db,
            adapter=DisabledOperationsDispatchAdapter(),
            worker_id="worker-disabled",
            batch_size=1,
        ) == 1
        record = db.query(OperationsDispatchOutboxRecord).one()
        assert record.status == OperationsDispatchStatus.FAILED.value
        assert record.error_category == "provider_adapter_disabled"
