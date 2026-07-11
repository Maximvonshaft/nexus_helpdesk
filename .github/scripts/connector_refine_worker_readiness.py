from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}_COUNT={count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "backend/app/settings.py",
    '        self.nexus_osr_required_workers = tuple(self._parse_csv(os.getenv("NEXUS_OSR_REQUIRED_WORKERS", "background_worker,outbound_worker,webchat_ai_worker,handoff_snapshot_worker,operations_dispatch_worker")))\n        self.nexus_osr_worker_stale_seconds = int(os.getenv("NEXUS_OSR_WORKER_STALE_SECONDS", "90"))\n',
    '        self.nexus_osr_required_workers = tuple(self._parse_csv(os.getenv("NEXUS_OSR_REQUIRED_WORKERS", "")))\n        self.worker_heartbeat_interval_seconds = int(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "30"))\n        self.nexus_osr_worker_stale_seconds = int(os.getenv("NEXUS_OSR_WORKER_STALE_SECONDS", "90"))\n',
    label="SETTINGS_WORKER_CONFIG",
)
replace_once(
    "backend/app/settings.py",
    '        if self.nexus_osr_worker_stale_seconds < 10 or self.nexus_osr_worker_stale_seconds > 3600:\n            raise RuntimeError("NEXUS_OSR_WORKER_STALE_SECONDS must be between 10 and 3600")\n',
    '        if self.worker_heartbeat_interval_seconds < 5 or self.worker_heartbeat_interval_seconds > 300:\n            raise RuntimeError("WORKER_HEARTBEAT_INTERVAL_SECONDS must be between 5 and 300")\n        if self.nexus_osr_worker_stale_seconds < 10 or self.nexus_osr_worker_stale_seconds > 3600:\n            raise RuntimeError("NEXUS_OSR_WORKER_STALE_SECONDS must be between 10 and 3600")\n        if self.nexus_osr_worker_stale_seconds <= self.worker_heartbeat_interval_seconds:\n            raise RuntimeError("NEXUS_OSR_WORKER_STALE_SECONDS must exceed WORKER_HEARTBEAT_INTERVAL_SECONDS")\n',
    label="SETTINGS_HEARTBEAT_VALIDATION",
)

replace_once(
    "backend/app/services/nexus_osr/business_readiness_service.py",
    'DEFAULT_REQUIRED_WORKERS = (\n    "background_worker",\n    "outbound_worker",\n    "webchat_ai_worker",\n    "handoff_snapshot_worker",\n    "operations_dispatch_worker",\n)\n',
    'SHADOW_REQUIRED_WORKERS = (\n    "background_worker",\n    "webchat_ai_worker",\n    "handoff_snapshot_worker",\n)\nWRITE_REQUIRED_WORKERS = (\n    "outbound_worker",\n    "operations_dispatch_worker",\n)\n',
    label="WORKER_PROFILE_CONSTANTS",
)
replace_once(
    "backend/app/services/nexus_osr/business_readiness_service.py",
    'def _worker_evidence(db: Session, *, now: datetime) -> CapabilityEvidence:\n    required_workers = _csv_env("NEXUS_OSR_REQUIRED_WORKERS", DEFAULT_REQUIRED_WORKERS)\n',
    'def _required_workers_for_profile(settings: Any, profile_name: str) -> tuple[str, ...]:\n    configured = tuple(getattr(settings, "nexus_osr_required_workers", ()) or ())\n    if configured:\n        return tuple(dict.fromkeys(str(item).strip()[:80] for item in configured if str(item).strip()))\n    if profile_name == "development":\n        return ()\n    if profile_name == "shadow":\n        return SHADOW_REQUIRED_WORKERS\n    return (*SHADOW_REQUIRED_WORKERS, *WRITE_REQUIRED_WORKERS)\n\n\ndef _worker_evidence(\n    db: Session,\n    *,\n    now: datetime,\n    required_workers: tuple[str, ...],\n) -> CapabilityEvidence:\n',
    label="WORKER_EVIDENCE_SIGNATURE",
)
replace_once(
    "backend/app/services/nexus_osr/business_readiness_service.py",
    '    expected_head = expected_migration_head or os.getenv("EXPECTED_MIGRATION_HEAD")\n\n    release_identity = runtime_identity_status(default_app_version="server")\n',
    '    expected_head = expected_migration_head or os.getenv("EXPECTED_MIGRATION_HEAD")\n    required_workers = _required_workers_for_profile(settings, profile.name.value)\n\n    release_identity = runtime_identity_status(default_app_version="server")\n',
    label="COLLECT_REQUIRED_WORKERS",
)
replace_once(
    "backend/app/services/nexus_osr/business_readiness_service.py",
    '        "workers": _worker_evidence(db, now=current),\n',
    '        "workers": _worker_evidence(db, now=current, required_workers=required_workers),\n',
    label="COLLECT_WORKER_EVIDENCE",
)
replace_once(
    "backend/app/services/nexus_osr/business_readiness_service.py",
    '        "required_workers": list(_csv_env("NEXUS_OSR_REQUIRED_WORKERS", DEFAULT_REQUIRED_WORKERS)),\n',
    '        "required_workers": list(required_workers),\n',
    label="EFFECTIVE_REQUIRED_WORKERS",
)

replace_once(
    "backend/tests/test_nexus_osr_business_readiness.py",
    '    DEFAULT_REQUIRED_WORKERS,\n',
    '    SHADOW_REQUIRED_WORKERS,\n    WRITE_REQUIRED_WORKERS,\n',
    label="TEST_IMPORT_WORKERS",
)
replace_once(
    "backend/tests/test_nexus_osr_business_readiness.py",
    'def _seed_governed_runtime(db, *, now):\n',
    'def _seed_governed_runtime(db, *, now, include_write_workers: bool = True):\n',
    label="TEST_SEED_SIGNATURE",
)
replace_once(
    "backend/tests/test_nexus_osr_business_readiness.py",
    '    for name in DEFAULT_REQUIRED_WORKERS:\n',
    '    worker_names = list(SHADOW_REQUIRED_WORKERS)\n    if include_write_workers:\n        worker_names.extend(WRITE_REQUIRED_WORKERS)\n    for name in worker_names:\n',
    label="TEST_SEED_WORKERS",
)
replace_once(
    "backend/tests/test_nexus_osr_business_readiness.py",
    '    _seed_governed_runtime(db_session, now=now)\n    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")\n\n    result = collect_business_readiness(\n',
    '    _seed_governed_runtime(db_session, now=now, include_write_workers=False)\n    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")\n\n    result = collect_business_readiness(\n',
    label="TEST_SHADOW_WORKERS",
)
replace_once(
    "backend/tests/test_nexus_osr_business_readiness.py",
    '    db_session.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == "operations_dispatch_worker").delete()\n',
    '    db_session.query(ServiceHeartbeat).filter(ServiceHeartbeat.service_name == "webchat_ai_worker").delete()\n',
    label="TEST_MISSING_WORKER",
)
replace_once(
    "backend/tests/test_nexus_osr_business_readiness.py",
    '            service_name="operations_dispatch_worker",\n',
    '            service_name="webchat_ai_worker",\n',
    label="TEST_STALE_WORKER",
)
