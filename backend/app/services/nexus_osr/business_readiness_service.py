from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from ...enums import JobStatus
from ...models import BackgroundJob, ServiceHeartbeat
from ...models_control_plane import KnowledgeItem
from ...models_operations_dispatch import OperationsDispatchOutboxRecord
from ...models_osr import EscalationPolicyRecord
from ...models_webchat_binding import WebchatPublicOriginBinding
from ...utils.time import ensure_utc, utc_now
from ..release_metadata import runtime_identity_status
from .release_profiles import (
    CapabilityEvidence,
    CapabilityStatus,
    ProfileEvaluation,
    evaluate_release_profile,
    get_release_profile,
)

SHADOW_REQUIRED_WORKERS = (
    "background_worker",
    "webchat_ai_worker",
    "handoff_snapshot_worker",
)
WRITE_REQUIRED_WORKERS = (
    "outbound_worker",
    "operations_dispatch_worker",
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 86400) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = tuple(dict.fromkeys(item.strip()[:80] for item in raw.split(",") if item.strip()))
    return values


def _age_seconds(value: datetime | None, *, now: datetime) -> int | None:
    resolved = ensure_utc(value)
    if resolved is None:
        return None
    return max(0, int((now - resolved).total_seconds()))


def _ready(reason: str, **details: Any) -> CapabilityEvidence:
    return CapabilityEvidence(CapabilityStatus.READY, reason, details=details or None)


def _degraded(reason: str, **details: Any) -> CapabilityEvidence:
    return CapabilityEvidence(CapabilityStatus.DEGRADED, reason, details=details or None)


def _not_ready(reason: str, **details: Any) -> CapabilityEvidence:
    return CapabilityEvidence(CapabilityStatus.NOT_READY, reason, details=details or None)


def _not_configured(reason: str, **details: Any) -> CapabilityEvidence:
    return CapabilityEvidence(CapabilityStatus.NOT_CONFIGURED, reason, details=details or None)


def _migration_evidence(*, observed: str | None, expected: str | None) -> CapabilityEvidence:
    observed_value = str(observed or "").strip() or None
    expected_value = str(expected or "").strip() or None
    if expected_value is None:
        return _not_configured("migration_identity.expected_head_missing", observed=observed_value)
    if observed_value is None:
        return _not_ready("migration_identity.observed_head_missing", expected=expected_value)
    if observed_value != expected_value:
        return _not_ready(
            "migration_identity.head_mismatch",
            observed=observed_value,
            expected=expected_value,
        )
    return _ready("migration_identity.head_matches", observed=observed_value)


def _tenant_binding_evidence(db: Session) -> CapabilityEvidence:
    active_count = (
        db.query(func.count(WebchatPublicOriginBinding.id))
        .filter(WebchatPublicOriginBinding.is_active.is_(True))
        .scalar()
        or 0
    )
    if active_count <= 0:
        return _not_configured("tenant_binding.no_active_origin_binding")
    return _ready("tenant_binding.active", active_bindings=int(active_count))


def _tracking_evidence(settings: Any) -> CapabilityEvidence:
    enabled = bool(getattr(settings, "webchat_tracking_fact_lookup_enabled", False))
    source = str(getattr(settings, "webchat_tracking_fact_source", "") or "").strip().lower()
    redaction = bool(getattr(settings, "webchat_tracking_fact_redaction_enabled", False))
    if not enabled:
        return _not_configured("tracking_truth.lookup_disabled")
    if not source:
        return _not_ready("tracking_truth.source_missing")
    if not redaction:
        return _not_ready("tracking_truth.redaction_disabled", source=source)
    return _ready("tracking_truth.configured", source=source, redaction=True)


def _knowledge_evidence(db: Session) -> CapabilityEvidence:
    published_count = (
        db.query(func.count(KnowledgeItem.id))
        .filter(
            KnowledgeItem.status == "published",
            KnowledgeItem.published_version > 0,
            KnowledgeItem.published_at.is_not(None),
            KnowledgeItem.visibility == "customer",
            KnowledgeItem.shareability == "customer_visible",
        )
        .scalar()
        or 0
    )
    stale_index_count = (
        db.query(func.count(KnowledgeItem.id))
        .filter(
            KnowledgeItem.status == "published",
            KnowledgeItem.published_version > KnowledgeItem.indexed_version,
        )
        .scalar()
        or 0
    )
    failed_parse_count = (
        db.query(func.count(KnowledgeItem.id))
        .filter(KnowledgeItem.parsing_status == "failed")
        .scalar()
        or 0
    )
    if published_count <= 0:
        return _not_configured("knowledge_runtime.no_published_customer_knowledge")
    if stale_index_count > 0:
        return _not_ready(
            "knowledge_runtime.published_index_drift",
            published=int(published_count),
            stale_index=int(stale_index_count),
        )
    if failed_parse_count > 0:
        return _degraded(
            "knowledge_runtime.parse_failures_present",
            published=int(published_count),
            failed_parse=int(failed_parse_count),
        )
    return _ready("knowledge_runtime.published_and_indexed", published=int(published_count))


def _escalation_evidence(db: Session, settings: Any) -> CapabilityEvidence:
    enabled = bool(getattr(settings, "osr_escalation_orchestration_enabled", False))
    policy_count = (
        db.query(func.count(EscalationPolicyRecord.id))
        .filter(EscalationPolicyRecord.enabled.is_(True))
        .scalar()
        or 0
    )
    if not enabled:
        return _not_configured("configured_escalation.runtime_disabled", enabled_policies=int(policy_count))
    if policy_count <= 0:
        return _not_ready("configured_escalation.no_enabled_policy")
    return _ready("configured_escalation.active", enabled_policies=int(policy_count))


def _required_workers_for_profile(settings: Any, profile_name: str) -> tuple[str, ...]:
    configured = tuple(getattr(settings, "nexus_osr_required_workers", ()) or ())
    if configured:
        return tuple(dict.fromkeys(str(item).strip()[:80] for item in configured if str(item).strip()))
    if profile_name == "development":
        return ()
    if profile_name == "shadow":
        return SHADOW_REQUIRED_WORKERS
    return (*SHADOW_REQUIRED_WORKERS, *WRITE_REQUIRED_WORKERS)


def _worker_evidence(
    db: Session,
    *,
    now: datetime,
    required_workers: tuple[str, ...],
) -> CapabilityEvidence:
    stale_after = _env_int("NEXUS_OSR_WORKER_STALE_SECONDS", 90, minimum=10, maximum=3600)
    if not required_workers:
        return _not_configured("workers.required_set_empty")
    rows = (
        db.query(ServiceHeartbeat)
        .filter(ServiceHeartbeat.service_name.in_(required_workers))
        .all()
    )
    by_name = {row.service_name: row for row in rows}
    missing = [name for name in required_workers if name not in by_name]
    stale: list[str] = []
    failed: list[str] = []
    ages: dict[str, int] = {}
    for name, row in by_name.items():
        age = _age_seconds(row.last_seen_at, now=now)
        if age is not None:
            ages[name] = age
        if str(row.status or "").lower() not in {"ok", "ready", "healthy"}:
            failed.append(name)
        elif age is None or age > stale_after:
            stale.append(name)
    if missing:
        return _not_configured(
            "workers.heartbeat_missing",
            missing=missing,
            required_count=len(required_workers),
        )
    if failed:
        return _not_ready("workers.reported_failure", failed=failed, ages=ages)
    if stale:
        return _not_ready("workers.heartbeat_stale", stale=stale, ages=ages, threshold_seconds=stale_after)
    return _ready("workers.heartbeats_fresh", count=len(rows), max_age_seconds=max(ages.values(), default=0))


def _background_queue_evidence(db: Session, *, now: datetime) -> CapabilityEvidence:
    warn_age = _env_int("NEXUS_OSR_QUEUE_WARN_AGE_SECONDS", 120, minimum=10, maximum=86400)
    fail_age = _env_int("NEXUS_OSR_QUEUE_FAIL_AGE_SECONDS", 600, minimum=warn_age, maximum=86400)
    pending_count = (
        db.query(func.count(BackgroundJob.id))
        .filter(BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing]))
        .scalar()
        or 0
    )
    dead_count = (
        db.query(func.count(BackgroundJob.id))
        .filter(BackgroundJob.status == JobStatus.dead)
        .scalar()
        or 0
    )
    oldest = (
        db.query(func.min(BackgroundJob.created_at))
        .filter(BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing]))
        .scalar()
    )
    oldest_age = _age_seconds(oldest, now=now) or 0
    if dead_count > 0:
        return _not_ready("background_queue.dead_jobs_present", dead=int(dead_count), pending=int(pending_count), oldest_seconds=oldest_age)
    if oldest_age >= fail_age:
        return _not_ready("background_queue.oldest_pending_exceeds_failure_budget", pending=int(pending_count), oldest_seconds=oldest_age, threshold_seconds=fail_age)
    if oldest_age >= warn_age:
        return _degraded("background_queue.oldest_pending_exceeds_warning_budget", pending=int(pending_count), oldest_seconds=oldest_age, threshold_seconds=warn_age)
    return _ready("background_queue.within_budget", pending=int(pending_count), oldest_seconds=oldest_age)


def _provider_evidence(db: Session, *, now: datetime) -> CapabilityEvidence:
    stale_after = _env_int("NEXUS_OSR_PROVIDER_HEARTBEAT_STALE_SECONDS", 120, minimum=10, maximum=3600)
    row = db.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == "provider_runtime").first()
    if row is None:
        return _not_configured("provider_runtime.heartbeat_missing")
    age = _age_seconds(row.last_seen_at, now=now)
    if str(row.status or "").lower() not in {"ok", "ready", "healthy"}:
        return _not_ready("provider_runtime.reported_failure", status=str(row.status or "unknown")[:40], age_seconds=age)
    if age is None or age > stale_after:
        return _not_ready("provider_runtime.heartbeat_stale", age_seconds=age, threshold_seconds=stale_after)
    return _ready("provider_runtime.heartbeat_fresh", age_seconds=age)


def _dispatch_evidence(db: Session, *, now: datetime) -> CapabilityEvidence:
    warn_age = _env_int("NEXUS_OSR_DISPATCH_WARN_AGE_SECONDS", 120, minimum=10, maximum=86400)
    fail_age = _env_int("NEXUS_OSR_DISPATCH_FAIL_AGE_SECONDS", 600, minimum=warn_age, maximum=86400)
    pending_statuses = ("pending", "processing", "retryable")
    pending_count = (
        db.query(func.count(OperationsDispatchOutboxRecord.id))
        .filter(OperationsDispatchOutboxRecord.status.in_(pending_statuses))
        .scalar()
        or 0
    )
    dead_count = (
        db.query(func.count(OperationsDispatchOutboxRecord.id))
        .filter(OperationsDispatchOutboxRecord.status.in_(("failed", "dead_letter")))
        .scalar()
        or 0
    )
    expired_lease_count = (
        db.query(func.count(OperationsDispatchOutboxRecord.id))
        .filter(
            OperationsDispatchOutboxRecord.status == "processing",
            OperationsDispatchOutboxRecord.lease_expires_at.is_not(None),
            OperationsDispatchOutboxRecord.lease_expires_at <= now,
        )
        .scalar()
        or 0
    )
    oldest = (
        db.query(func.min(OperationsDispatchOutboxRecord.created_at))
        .filter(OperationsDispatchOutboxRecord.status.in_(pending_statuses))
        .scalar()
    )
    oldest_age = _age_seconds(oldest, now=now) or 0
    if dead_count > 0:
        return _not_ready("dispatch_outbox.failed_or_dead_letter_present", failed_or_dead=int(dead_count), pending=int(pending_count))
    if expired_lease_count > 0:
        return _not_ready("dispatch_outbox.expired_processing_lease", expired_leases=int(expired_lease_count))
    if oldest_age >= fail_age:
        return _not_ready("dispatch_outbox.oldest_pending_exceeds_failure_budget", pending=int(pending_count), oldest_seconds=oldest_age)
    if oldest_age >= warn_age:
        return _degraded("dispatch_outbox.oldest_pending_exceeds_warning_budget", pending=int(pending_count), oldest_seconds=oldest_age)
    return _ready("dispatch_outbox.within_budget", pending=int(pending_count), oldest_seconds=oldest_age)


def _external_write_evidence(settings: Any) -> CapabilityEvidence:
    enabled = bool(getattr(settings, "enable_outbound_dispatch", False)) or bool(getattr(settings, "whatsapp_native_enabled", False)) or bool(getattr(settings, "outbound_email_production_pilot_enabled", False))
    if enabled:
        return _ready("external_writes.enabled")
    return _not_configured("external_writes.disabled")


def _observability_evidence(settings: Any) -> CapabilityEvidence:
    enabled = bool(getattr(settings, "metrics_enabled", False))
    token_present = bool(getattr(settings, "metrics_token", None))
    if not enabled:
        return _not_configured("observability.metrics_disabled")
    if not token_present:
        return _not_ready("observability.metrics_token_missing")
    return _ready("observability.metrics_configured")


def collect_business_readiness(
    db: Session,
    *,
    settings: Any,
    observed_migration_head: str | None,
    expected_migration_head: str | None = None,
    storage_ready: bool,
    runtime_signing_ready: bool,
    database_ready: bool = True,
    now: datetime | None = None,
    profile_name: str | None = None,
) -> ProfileEvaluation:
    current = ensure_utc(now) or utc_now()
    profile_value = profile_name or os.getenv("NEXUS_OSR_RELEASE_PROFILE", "development")
    profile = get_release_profile(profile_value)
    expected_head = expected_migration_head or os.getenv("EXPECTED_MIGRATION_HEAD")

    required_workers = _required_workers_for_profile(settings, profile.name.value)

    release_identity = runtime_identity_status(default_app_version="server")
    evidence: dict[str, CapabilityEvidence] = {
        "database": _ready("database.connection_ok") if database_ready else _not_ready("database.connection_failed"),
        "migration_identity": _migration_evidence(observed=observed_migration_head, expected=expected_head),
        "storage": _ready("storage.ready") if storage_ready else _not_ready("storage.not_ready"),
        "runtime_contract_signing": _ready("runtime_contract_signing.ready") if runtime_signing_ready else _not_ready("runtime_contract_signing.not_ready"),
        "tenant_binding": _tenant_binding_evidence(db),
        "tracking_truth": _tracking_evidence(settings),
        "knowledge_runtime": _knowledge_evidence(db),
        "configured_escalation": _escalation_evidence(db, settings),
        "workers": _worker_evidence(db, now=current, required_workers=required_workers),
        "background_queue": _background_queue_evidence(db, now=current),
        "provider_runtime": _provider_evidence(db, now=current),
        "dispatch_outbox": _dispatch_evidence(db, now=current),
        "external_writes": _external_write_evidence(settings),
        "observability": _observability_evidence(settings),
    }
    effective_config: Mapping[str, Any] = {
        "profile": profile.name.value,
        "expected_migration_head": expected_head,
        "release_metadata_complete": release_identity.get("release_metadata_complete"),
        "git_sha": release_identity.get("git_sha"),
        "image_tag": release_identity.get("image_tag"),
        "tracking_enabled": bool(getattr(settings, "webchat_tracking_fact_lookup_enabled", False)),
        "tracking_source": str(getattr(settings, "webchat_tracking_fact_source", "") or "")[:80],
        "knowledge_runtime_version": str(getattr(settings, "knowledge_runtime_version", "") or "")[:40],
        "configured_escalation_enabled": bool(getattr(settings, "osr_escalation_orchestration_enabled", False)),
        "outbound_dispatch_enabled": bool(getattr(settings, "enable_outbound_dispatch", False)),
        "required_workers": list(required_workers),
    }
    return evaluate_release_profile(profile, evidence, configuration=effective_config)
