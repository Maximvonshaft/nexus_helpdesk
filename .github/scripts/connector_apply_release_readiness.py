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
    '        self.app_env = os.getenv("APP_ENV", "development").strip().lower() or "development"\n',
    '        self.app_env = os.getenv("APP_ENV", "development").strip().lower() or "development"\n'
    '        self.nexus_osr_release_profile = os.getenv("NEXUS_OSR_RELEASE_PROFILE", "development").strip().lower() or "development"\n'
    '        self.expected_migration_head = os.getenv("EXPECTED_MIGRATION_HEAD", "").strip() or None\n',
    label="SETTINGS_PROFILE_FIELDS",
)
replace_once(
    "backend/app/settings.py",
    '        self.worker_poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "2"))\n',
    '        self.worker_poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "2"))\n'
    '        self.nexus_osr_required_workers = tuple(self._parse_csv(os.getenv("NEXUS_OSR_REQUIRED_WORKERS", "background_worker,outbound_worker,webchat_ai_worker,handoff_snapshot_worker,operations_dispatch_worker")))\n'
    '        self.nexus_osr_worker_stale_seconds = int(os.getenv("NEXUS_OSR_WORKER_STALE_SECONDS", "90"))\n'
    '        self.nexus_osr_queue_warn_age_seconds = int(os.getenv("NEXUS_OSR_QUEUE_WARN_AGE_SECONDS", "120"))\n'
    '        self.nexus_osr_queue_fail_age_seconds = int(os.getenv("NEXUS_OSR_QUEUE_FAIL_AGE_SECONDS", "600"))\n'
    '        self.nexus_osr_dispatch_warn_age_seconds = int(os.getenv("NEXUS_OSR_DISPATCH_WARN_AGE_SECONDS", "120"))\n'
    '        self.nexus_osr_dispatch_fail_age_seconds = int(os.getenv("NEXUS_OSR_DISPATCH_FAIL_AGE_SECONDS", "600"))\n',
    label="SETTINGS_READINESS_THRESHOLDS",
)
replace_once(
    "backend/app/settings.py",
    '        if self.webchat_tracking_fact_lookup_enabled and not self.webchat_tracking_fact_redaction_enabled:\n            raise RuntimeError("WEBCHAT_TRACKING_FACT_REDACTION_ENABLED must be true when tracking lookup is enabled")\n',
    '        if self.webchat_tracking_fact_lookup_enabled and not self.webchat_tracking_fact_redaction_enabled:\n            raise RuntimeError("WEBCHAT_TRACKING_FACT_REDACTION_ENABLED must be true when tracking lookup is enabled")\n'
    '        if self.nexus_osr_release_profile not in {"development", "shadow", "pilot", "full_osr"}:\n            raise RuntimeError("NEXUS_OSR_RELEASE_PROFILE must be development, shadow, pilot, or full_osr")\n'
    '        if not self.nexus_osr_required_workers:\n            raise RuntimeError("NEXUS_OSR_REQUIRED_WORKERS must declare at least one worker")\n'
    '        if self.nexus_osr_worker_stale_seconds < 10 or self.nexus_osr_worker_stale_seconds > 3600:\n            raise RuntimeError("NEXUS_OSR_WORKER_STALE_SECONDS must be between 10 and 3600")\n'
    '        if self.nexus_osr_queue_warn_age_seconds < 10 or self.nexus_osr_queue_fail_age_seconds < self.nexus_osr_queue_warn_age_seconds:\n            raise RuntimeError("NEXUS_OSR queue age thresholds are invalid")\n'
    '        if self.nexus_osr_dispatch_warn_age_seconds < 10 or self.nexus_osr_dispatch_fail_age_seconds < self.nexus_osr_dispatch_warn_age_seconds:\n            raise RuntimeError("NEXUS_OSR dispatch age thresholds are invalid")\n',
    label="SETTINGS_READINESS_VALIDATION",
)
replace_once(
    "backend/app/settings.py",
    '        if self.app_env == "production":\n            if not self.jwt_secret_key:\n',
    '        if self.app_env == "production":\n'
    '            if not self.expected_migration_head:\n                raise RuntimeError("EXPECTED_MIGRATION_HEAD must be set in production")\n'
    '            if self.nexus_osr_release_profile == "development":\n                raise RuntimeError("NEXUS_OSR_RELEASE_PROFILE=development is not allowed in production")\n'
    '            if self.nexus_osr_release_profile in {"shadow", "pilot", "full_osr"} and not self.osr_escalation_orchestration_enabled:\n                raise RuntimeError("OSR_ESCALATION_ORCHESTRATION_ENABLED=true is required for governed production profiles")\n'
    '            if not self.jwt_secret_key:\n',
    label="SETTINGS_PRODUCTION_PROFILE",
)

replace_once(
    "backend/app/main.py",
    'from .api.osr_admin import router as osr_admin_router\n',
    'from .api.osr_admin import router as osr_admin_router\nfrom .api.osr_readiness import router as osr_readiness_router\n',
    label="MAIN_READINESS_IMPORT",
)
replace_once(
    "backend/app/main.py",
    """        ready = storage_readiness.ok and bool(frontend_readiness['ok']) and bool(runtime_contract_readiness['ok'])
        payload = {
            'status': 'ready' if ready else 'not_ready',
            'database': 'ok',
            'migration_revision': migration_revision,
""",
    """        expected_migration_head = settings.expected_migration_head
        migration_identity_ready = bool(
            expected_migration_head
            and migration_revision
            and migration_revision == expected_migration_head
        ) if settings.app_env == 'production' or expected_migration_head else True
        ready = (
            storage_readiness.ok
            and bool(frontend_readiness['ok'])
            and bool(runtime_contract_readiness['ok'])
            and migration_identity_ready
        )
        payload = {
            'status': 'ready' if ready else 'not_ready',
            'database': 'ok',
            'migration_revision': migration_revision,
            'expected_migration_head': expected_migration_head,
            'migration_identity': 'ready' if migration_identity_ready else 'not_ready',
            'release_profile': settings.nexus_osr_release_profile,
""",
    label="MAIN_READYZ_IDENTITY",
)
replace_once(
    "backend/app/main.py",
    """        if not storage_readiness.ok:
            app_log_event(40, 'readiness_storage_check_failed', storage=storage_readiness.as_dict())
""",
    """        if not migration_identity_ready:
            app_log_event(
                40,
                'readiness_migration_identity_failed',
                observed_migration_head=migration_revision,
                expected_migration_head=expected_migration_head,
            )
            return JSONResponse(status_code=503, content=payload)
        if not storage_readiness.ok:
            app_log_event(40, 'readiness_storage_check_failed', storage=storage_readiness.as_dict())
""",
    label="MAIN_READYZ_MIGRATION_FAILURE",
)
replace_once(
    "backend/app/main.py",
    'app.include_router(osr_admin_router)\n',
    'app.include_router(osr_admin_router)\napp.include_router(osr_readiness_router)\n',
    label="MAIN_READINESS_ROUTER",
)
