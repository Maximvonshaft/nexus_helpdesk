from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.model_registry import register_all_models
from app.models import ServiceHeartbeat
from app.models_operations_dispatch import OperationsDispatchOutboxRecord
from app.models_osr import WhatsAppRoutingRuleRecord
from app.operations_dispatch_runtime.adapters import (
    AdapterRegistry,
    RegisteredAdapter,
)
from app.operations_dispatch_runtime.config import OperationsDispatchRuntimeConfig
from app.operations_dispatch_runtime.worker import run_operations_dispatch_cycle
from app.services.nexus_osr.operations_dispatch_processor import (
    OperationsDispatchAdapterResult,
    OperationsDispatchEnvelope,
)


def _session(tmp_path):
    register_all_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'dispatch-runtime.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)(), engine


def _pending(db, *, dispatch_key: str = "ops-dispatch:" + "a" * 64, tenant: str = "tenant-a"):
    rule = WhatsAppRoutingRuleRecord(
        country_code="ME",
        issue_type=f"address-{dispatch_key[-6:]}",
        channel="whatsapp",
        destination_group_id="governed-group-alias",
        priority=100,
        enabled=True,
    )
    db.add(rule)
    db.flush()
    record = OperationsDispatchOutboxRecord(
        dispatch_key=dispatch_key,
        tenant_key=tenant,
        country_code="ME",
        channel_key="whatsapp",
        routing_rule_id=rule.id,
        destination_group_key="group:operations-primary",
        destination_group_hash="sha256:" + "b" * 64,
        status="pending",
        attempt_count=0,
        max_attempts=5,
    )
    db.add(record)
    db.commit()
    return record


@dataclass
class SyntheticAcceptedAdapter:
    provider: str = "synthetic"
    calls: list[str] = field(default_factory=list)

    def dispatch(self, envelope: OperationsDispatchEnvelope) -> OperationsDispatchAdapterResult:
        self.calls.append(envelope.dispatch_key)
        return OperationsDispatchAdapterResult(
            success=True,
            acknowledgement={
                "schema": "nexus.operations_dispatch.ack.v1",
                "accepted": True,
                "dispatch_key": envelope.dispatch_key,
                "provider": self.provider,
                "receipt_id": "receipt:synthetic-001",
            },
        )


@dataclass
class SyntheticInvalidAckAdapter:
    calls: list[str] = field(default_factory=list)

    def dispatch(self, envelope: OperationsDispatchEnvelope) -> OperationsDispatchAdapterResult:
        self.calls.append(envelope.dispatch_key)
        return OperationsDispatchAdapterResult(
            success=True,
            acknowledgement={
                "schema": "nexus.operations_dispatch.ack.v1",
                "accepted": True,
                "dispatch_key": "wrong-dispatch-key",
                "provider": "synthetic",
                "receipt_id": "receipt:wrong",
            },
        )


def _enabled_config(**overrides) -> OperationsDispatchRuntimeConfig:
    values = {
        "mode": "enabled",
        "adapter_name": "synthetic",
        "app_env": "test",
        "tenant_authority_ready": True,
        "batch_size": 10,
        "lease_seconds": 120,
    }
    values.update(overrides)
    return OperationsDispatchRuntimeConfig(**values)


def _registry(adapter) -> AdapterRegistry:
    return AdapterRegistry((
        RegisteredAdapter(
            name="synthetic",
            factory=lambda: adapter,
            allowed_environments=frozenset({"test"}),
        ),
    ))


def test_disabled_runtime_does_not_claim_outbox(tmp_path) -> None:
    db, engine = _session(tmp_path)
    try:
        record = _pending(db)
        result = run_operations_dispatch_cycle(
            db,
            config=OperationsDispatchRuntimeConfig(),
            registry=AdapterRegistry(),
            worker_id="worker-operations-dispatch",
        )
        db.refresh(record)
        assert result.status == "blocked"
        assert result.reason == "operations_dispatch_mode_disabled"
        assert record.status == "pending"
        assert record.attempt_count == 0
        heartbeat = db.query(ServiceHeartbeat).filter_by(service_name="operations_dispatch_worker").one()
        assert heartbeat.status == "blocked"
        assert heartbeat.details_json["mode"] == "disabled"
        assert "payload" not in str(heartbeat.details_json).lower()
    finally:
        db.close()
        engine.dispose()


def test_missing_tenant_authority_does_not_claim_or_call_adapter(tmp_path) -> None:
    db, engine = _session(tmp_path)
    adapter = SyntheticAcceptedAdapter()
    try:
        record = _pending(db, dispatch_key="ops-dispatch:" + "c" * 64)
        result = run_operations_dispatch_cycle(
            db,
            config=_enabled_config(tenant_authority_ready=False),
            registry=_registry(adapter),
            worker_id="worker-operations-dispatch",
        )
        db.refresh(record)
        assert result.status == "blocked"
        assert result.reason == "operations_dispatch_tenant_authority_unavailable"
        assert record.status == "pending"
        assert adapter.calls == []
    finally:
        db.close()
        engine.dispose()


def test_valid_ack_dispatches_once_and_stable_key_prevents_replay(tmp_path) -> None:
    db, engine = _session(tmp_path)
    adapter = SyntheticAcceptedAdapter()
    try:
        record = _pending(db, dispatch_key="ops-dispatch:" + "d" * 64)
        first = run_operations_dispatch_cycle(
            db,
            config=_enabled_config(),
            registry=_registry(adapter),
            worker_id="worker-operations-dispatch",
        )
        db.refresh(record)
        assert first.processed == 1
        assert record.status == "dispatched"
        assert record.provider_acknowledgement
        assert record.external_reference_safe
        assert adapter.calls == [record.dispatch_key]

        second = run_operations_dispatch_cycle(
            db,
            config=_enabled_config(),
            registry=_registry(adapter),
            worker_id="worker-operations-dispatch",
        )
        assert second.processed == 0
        assert adapter.calls == [record.dispatch_key]
        heartbeat = db.query(ServiceHeartbeat).filter_by(service_name="operations_dispatch_worker").one()
        assert heartbeat.status == "ok"
        assert heartbeat.details_json["last_success_at"]
    finally:
        db.close()
        engine.dispose()


def test_transport_success_without_matching_ack_is_not_completion(tmp_path) -> None:
    db, engine = _session(tmp_path)
    adapter = SyntheticInvalidAckAdapter()
    try:
        record = _pending(db, dispatch_key="ops-dispatch:" + "e" * 64)
        result = run_operations_dispatch_cycle(
            db,
            config=_enabled_config(),
            registry=_registry(adapter),
            worker_id="worker-operations-dispatch",
        )
        db.refresh(record)
        assert result.processed == 1
        assert record.status == "failed"
        assert record.error_category == "provider_ack_invalid"
        assert record.dispatched_at is None
        assert adapter.calls == [record.dispatch_key]
    finally:
        db.close()
        engine.dispose()


def test_default_tenant_never_reaches_provider(tmp_path) -> None:
    db, engine = _session(tmp_path)
    adapter = SyntheticAcceptedAdapter()
    try:
        record = _pending(
            db,
            dispatch_key="ops-dispatch:" + "f" * 64,
            tenant="default",
        )
        result = run_operations_dispatch_cycle(
            db,
            config=_enabled_config(),
            registry=_registry(adapter),
            worker_id="worker-operations-dispatch",
        )
        db.refresh(record)
        assert result.processed == 1
        assert record.status == "failed"
        assert record.error_category == "provider_ack_invalid"
        assert adapter.calls == []
    finally:
        db.close()
        engine.dispose()


def test_registry_rejects_adapter_outside_allowed_environment() -> None:
    adapter = SyntheticAcceptedAdapter()
    registry = _registry(adapter)
    config = _enabled_config(app_env="production")
    try:
        registry.resolve(config)
    except RuntimeError as exc:
        assert str(exc) == "operations_dispatch_adapter_environment_forbidden"
    else:
        raise AssertionError("test adapter must not resolve in production")
