from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..services.heartbeat_service import update_service_heartbeat
from ..services.nexus_osr.operations_dispatch_processor import process_operations_dispatch_batch
from ..utils.time import utc_now
from .adapters import AcknowledgementValidatingAdapter, AdapterRegistry, AdapterResolutionError
from .config import OperationsDispatchRuntimeConfig

SERVICE_NAME = "operations_dispatch_worker"


@dataclass(frozen=True)
class OperationsDispatchCycleResult:
    status: str
    processed: int
    reason: str
    adapter_name: str

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "processed": self.processed,
            "reason": self.reason,
            "adapter_name": self.adapter_name,
            "ready": self.ready,
        }


def run_operations_dispatch_cycle(
    db: Session,
    *,
    config: OperationsDispatchRuntimeConfig,
    registry: AdapterRegistry,
    worker_id: str,
) -> OperationsDispatchCycleResult:
    resolved = config.validated()
    instance_id = _worker_id(worker_id)

    if resolved.mode == "disabled":
        result = OperationsDispatchCycleResult(
            status="blocked",
            processed=0,
            reason="operations_dispatch_mode_disabled",
            adapter_name="disabled",
        )
        _write_heartbeat(db, instance_id=instance_id, config=resolved, result=result)
        db.commit()
        return result

    if not resolved.tenant_authority_ready:
        result = OperationsDispatchCycleResult(
            status="blocked",
            processed=0,
            reason="operations_dispatch_tenant_authority_unavailable",
            adapter_name=resolved.adapter_name,
        )
        _write_heartbeat(db, instance_id=instance_id, config=resolved, result=result)
        db.commit()
        return result

    try:
        inner = registry.resolve(resolved)
    except AdapterResolutionError as exc:
        result = OperationsDispatchCycleResult(
            status="blocked",
            processed=0,
            reason=str(exc)[:120],
            adapter_name=resolved.adapter_name,
        )
        _write_heartbeat(db, instance_id=instance_id, config=resolved, result=result)
        db.commit()
        return result

    adapter = AcknowledgementValidatingAdapter(inner)
    try:
        processed = process_operations_dispatch_batch(
            db,
            adapter=adapter,
            worker_id=instance_id,
            batch_size=resolved.batch_size,
            lease_seconds=resolved.lease_seconds,
        )
    except Exception:
        db.rollback()
        result = OperationsDispatchCycleResult(
            status="error",
            processed=0,
            reason="operations_dispatch_cycle_failed",
            adapter_name=resolved.adapter_name,
        )
        _write_heartbeat(db, instance_id=instance_id, config=resolved, result=result)
        db.commit()
        raise

    result = OperationsDispatchCycleResult(
        status="ready",
        processed=processed,
        reason="operations_dispatch_cycle_complete",
        adapter_name=resolved.adapter_name,
    )
    _write_heartbeat(db, instance_id=instance_id, config=resolved, result=result)
    db.commit()
    return result


def _write_heartbeat(
    db: Session,
    *,
    instance_id: str,
    config: OperationsDispatchRuntimeConfig,
    result: OperationsDispatchCycleResult,
) -> None:
    now = utc_now()
    details: dict[str, object] = {
        "schema": "nexus.operations_dispatch.worker_heartbeat.v1",
        "mode": config.mode,
        "adapter_name": result.adapter_name,
        "tenant_authority_ready": config.tenant_authority_ready,
        "processed": max(0, int(result.processed)),
        "reason": result.reason[:120],
    }
    if result.ready:
        details["last_success_at"] = now.isoformat()
    update_service_heartbeat(
        db,
        service_name=SERVICE_NAME,
        instance_id=instance_id,
        status="ok" if result.ready else result.status,
        details=details,
    )


def _worker_id(value: object) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > 120:
        raise ValueError("operations_dispatch_worker_id_invalid")
    if any(not (char.isalnum() or char in "._:-") for char in resolved):
        raise ValueError("operations_dispatch_worker_id_invalid")
    return resolved
