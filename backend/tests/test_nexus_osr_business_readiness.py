from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, models_control_plane, models_operations_dispatch, models_osr, models_webchat_binding, webchat_models  # noqa: F401
from app.db import Base
from app.enums import JobStatus
from app.models import BackgroundJob, ServiceHeartbeat
from app.models_control_plane import KnowledgeItem
from app.models_operations_dispatch import OperationsDispatchOutboxRecord
from app.models_osr import EscalationPolicyRecord, WhatsAppRoutingRuleRecord
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.services.nexus_osr.business_readiness_service import (
    DEFAULT_REQUIRED_WORKERS,
    collect_business_readiness,
)
from app.services.nexus_osr.release_profiles import CapabilityStatus
from app.utils.time import utc_now


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'business-readiness.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _settings(**overrides):
    values = {
        "webchat_tracking_fact_lookup_enabled": True,
        "webchat_tracking_fact_source": "speedaf_api",
        "webchat_tracking_fact_redaction_enabled": True,
        "knowledge_runtime_version": "v2",
        "osr_escalation_orchestration_enabled": True,
        "enable_outbound_dispatch": False,
        "whatsapp_native_enabled": False,
        "outbound_email_production_pilot_enabled": False,
        "metrics_enabled": True,
        "metrics_token": "configured",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _seed_governed_runtime(db, *, now):
    db.add(
        WebchatPublicOriginBinding(
            normalized_origin="https://support.example.test",
            tenant_key="tenant-a",
            channel_key="webchat",
            is_active=True,
        )
    )
    db.add(
        KnowledgeItem(
            item_key="published-a",
            title="Published customer knowledge",
            status="published",
            tenant_id="tenant-a",
            visibility="customer",
            shareability="customer_visible",
            published_body="Safe answer",
            published_normalized_text="safe answer",
            published_version=1,
            indexed_version=1,
            published_at=now,
            indexed_at=now,
            parsing_status="parsed",
        )
    )
    db.add(
        EscalationPolicyRecord(
            risk_key="complaint",
            country_code="GLOBAL",
            channel="all",
            enabled=True,
        )
    )
    for name in DEFAULT_REQUIRED_WORKERS:
        db.add(
            ServiceHeartbeat(
                service_name=name,
                instance_id=f"{name}-1",
                status="ok",
                details_json={"last_processed": 1},
                last_seen_at=now,
            )
        )
    db.add(
        ServiceHeartbeat(
            service_name="provider_runtime",
            instance_id="provider-1",
            status="ok",
            details_json={"probe": "safe"},
            last_seen_at=now,
        )
    )
    db.flush()


def test_shadow_profile_can_be_ready_only_with_governed_read_path(db_session, monkeypatch):
    now = utc_now()
    _seed_governed_runtime(db_session, now=now)
    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")

    result = collect_business_readiness(
        db_session,
        settings=_settings(),
        observed_migration_head="20260711_0058",
        storage_ready=True,
        runtime_signing_ready=True,
        profile_name="shadow",
        now=now,
    )

    assert result.status == CapabilityStatus.READY
    assert result.ready is True
    assert result.capabilities["external_writes"]["status"] == "not_configured"
    assert result.capabilities["workers"]["status"] == "ready"
    assert result.capabilities["knowledge_runtime"]["status"] == "ready"


def test_full_osr_is_not_ready_when_external_writes_are_disabled(db_session, monkeypatch):
    now = utc_now()
    _seed_governed_runtime(db_session, now=now)
    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")

    result = collect_business_readiness(
        db_session,
        settings=_settings(),
        observed_migration_head="20260711_0058",
        storage_ready=True,
        runtime_signing_ready=True,
        profile_name="full_osr",
        now=now,
    )

    assert result.status == CapabilityStatus.NOT_READY
    assert "external_writes.disabled" in result.reasons


def test_missing_and_stale_worker_heartbeats_fail_closed(db_session, monkeypatch):
    now = utc_now()
    _seed_governed_runtime(db_session, now=now)
    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")
    db_session.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == "operations_dispatch_worker").delete()
    db_session.flush()

    missing = collect_business_readiness(
        db_session,
        settings=_settings(),
        observed_migration_head="20260711_0058",
        storage_ready=True,
        runtime_signing_ready=True,
        profile_name="shadow",
        now=now,
    )
    assert missing.status == CapabilityStatus.NOT_READY
    assert "workers.heartbeat_missing" in missing.reasons

    db_session.add(
        ServiceHeartbeat(
            service_name="operations_dispatch_worker",
            status="ok",
            last_seen_at=now - timedelta(minutes=10),
        )
    )
    db_session.flush()
    stale = collect_business_readiness(
        db_session,
        settings=_settings(),
        observed_migration_head="20260711_0058",
        storage_ready=True,
        runtime_signing_ready=True,
        profile_name="shadow",
        now=now,
    )
    assert stale.status == CapabilityStatus.NOT_READY
    assert "workers.heartbeat_stale" in stale.reasons


def test_migration_mismatch_and_unconfigured_tracking_are_not_ready(db_session, monkeypatch):
    now = utc_now()
    _seed_governed_runtime(db_session, now=now)
    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")

    result = collect_business_readiness(
        db_session,
        settings=_settings(webchat_tracking_fact_lookup_enabled=False),
        observed_migration_head="20260711_0057",
        storage_ready=True,
        runtime_signing_ready=True,
        profile_name="shadow",
        now=now,
    )

    assert result.status == CapabilityStatus.NOT_READY
    assert "migration_identity.head_mismatch" in result.reasons
    assert "tracking_truth.lookup_disabled" in result.reasons


def test_queue_dead_jobs_and_dispatch_dead_letters_block_pilot(db_session, monkeypatch):
    now = utc_now()
    _seed_governed_runtime(db_session, now=now)
    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")
    db_session.add(
        BackgroundJob(
            queue_name="default",
            job_type="poison",
            payload_json="{}",
            status=JobStatus.dead,
        )
    )
    routing = WhatsAppRoutingRuleRecord(
        country_code="CH",
        issue_type="complaint",
        channel="whatsapp",
        destination_group_id="group-safe",
        enabled=True,
    )
    db_session.add(routing)
    db_session.flush()
    db_session.add(
        OperationsDispatchOutboxRecord(
            dispatch_key="dispatch-dead-1",
            tenant_key="tenant-a",
            country_code="CH",
            channel_key="whatsapp",
            routing_rule_id=routing.id,
            destination_group_key="group-safe",
            destination_group_hash="sha256:safe",
            status="dead_letter",
            max_attempts=5,
            attempt_count=5,
        )
    )
    db_session.flush()

    result = collect_business_readiness(
        db_session,
        settings=_settings(enable_outbound_dispatch=True),
        observed_migration_head="20260711_0058",
        storage_ready=True,
        runtime_signing_ready=True,
        profile_name="pilot",
        now=now,
    )

    assert result.status == CapabilityStatus.NOT_READY
    assert "background_queue.dead_jobs_present" in result.reasons
    assert "dispatch_outbox.failed_or_dead_letter_present" in result.reasons


def test_output_is_bounded_and_never_contains_heartbeat_secrets(db_session, monkeypatch):
    now = utc_now()
    _seed_governed_runtime(db_session, now=now)
    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")
    row = db_session.query(ServiceHeartbeat).filter_by(service_name="provider_runtime").one()
    row.details_json = {"authorization": "Bearer " + ("A" * 30), "safe_count": 2}
    db_session.flush()

    result = collect_business_readiness(
        db_session,
        settings=_settings(),
        observed_migration_head="20260711_0058",
        storage_ready=True,
        runtime_signing_ready=True,
        profile_name="shadow",
        now=now,
    ).as_dict()

    encoded = str(result)
    assert "Bearer " not in encoded
    assert len(result["reasons"]) <= 50
    assert result["configuration_hash"].startswith("sha256:")
