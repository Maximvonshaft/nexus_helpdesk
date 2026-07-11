from __future__ import annotations

from pathlib import Path
import re


def rewrite(path: str, transform) -> None:
    file_path = Path(path)
    before = file_path.read_text(encoding="utf-8")
    after = transform(before)
    if after != before:
        file_path.write_text(after, encoding="utf-8")


def settings_transform(text: str) -> str:
    if "WORKER_HEARTBEAT_INTERVAL_SECONDS" not in text:
        text, count = re.subn(
            r'        self\.nexus_osr_required_workers = tuple\(self\._parse_csv\(os\.getenv\("NEXUS_OSR_REQUIRED_WORKERS", ".*?"\)\)\)\n'
            r'        self\.nexus_osr_worker_stale_seconds = int\(os\.getenv\("NEXUS_OSR_WORKER_STALE_SECONDS", "90"\)\)\n',
            '        self.nexus_osr_required_workers = tuple(self._parse_csv(os.getenv("NEXUS_OSR_REQUIRED_WORKERS", "")))\n'
            '        self.worker_heartbeat_interval_seconds = int(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "30"))\n'
            '        self.nexus_osr_worker_stale_seconds = int(os.getenv("NEXUS_OSR_WORKER_STALE_SECONDS", "90"))\n',
            text,
            count=1,
        )
        if count != 1:
            raise SystemExit("SETTINGS_WORKER_CONFIG_NOT_FOUND")
    if "NEXUS_OSR_WORKER_STALE_SECONDS must exceed WORKER_HEARTBEAT_INTERVAL_SECONDS" not in text:
        anchor = (
            '        if self.nexus_osr_worker_stale_seconds < 10 or self.nexus_osr_worker_stale_seconds > 3600:\n'
            '            raise RuntimeError("NEXUS_OSR_WORKER_STALE_SECONDS must be between 10 and 3600")\n'
        )
        replacement = (
            '        if self.worker_heartbeat_interval_seconds < 5 or self.worker_heartbeat_interval_seconds > 300:\n'
            '            raise RuntimeError("WORKER_HEARTBEAT_INTERVAL_SECONDS must be between 5 and 300")\n'
            + anchor
            + '        if self.nexus_osr_worker_stale_seconds <= self.worker_heartbeat_interval_seconds:\n'
              '            raise RuntimeError("NEXUS_OSR_WORKER_STALE_SECONDS must exceed WORKER_HEARTBEAT_INTERVAL_SECONDS")\n'
        )
        if text.count(anchor) != 1:
            raise SystemExit("SETTINGS_HEARTBEAT_VALIDATION_NOT_FOUND")
        text = text.replace(anchor, replacement, 1)
    return text


def collector_transform(text: str) -> str:
    if "SHADOW_REQUIRED_WORKERS" not in text:
        text, count = re.subn(
            r'DEFAULT_REQUIRED_WORKERS = \(.*?\n\)\n',
            'SHADOW_REQUIRED_WORKERS = (\n'
            '    "background_worker",\n'
            '    "webchat_ai_worker",\n'
            '    "handoff_snapshot_worker",\n'
            ')\n'
            'WRITE_REQUIRED_WORKERS = (\n'
            '    "outbound_worker",\n'
            '    "operations_dispatch_worker",\n'
            ')\n',
            text,
            count=1,
            flags=re.DOTALL,
        )
        if count != 1:
            raise SystemExit("WORKER_CONSTANT_BLOCK_NOT_FOUND")
    if "def _required_workers_for_profile" not in text:
        pattern = r'def _worker_evidence\(db: Session, \*, now: datetime\) -> CapabilityEvidence:\n    required_workers = _csv_env\("NEXUS_OSR_REQUIRED_WORKERS", DEFAULT_REQUIRED_WORKERS\)\n'
        replacement = (
            'def _required_workers_for_profile(settings: Any, profile_name: str) -> tuple[str, ...]:\n'
            '    configured = tuple(getattr(settings, "nexus_osr_required_workers", ()) or ())\n'
            '    if configured:\n'
            '        return tuple(dict.fromkeys(str(item).strip()[:80] for item in configured if str(item).strip()))\n'
            '    if profile_name == "development":\n'
            '        return ()\n'
            '    if profile_name == "shadow":\n'
            '        return SHADOW_REQUIRED_WORKERS\n'
            '    return (*SHADOW_REQUIRED_WORKERS, *WRITE_REQUIRED_WORKERS)\n\n\n'
            'def _worker_evidence(\n'
            '    db: Session,\n'
            '    *,\n'
            '    now: datetime,\n'
            '    required_workers: tuple[str, ...],\n'
            ') -> CapabilityEvidence:\n'
        )
        text, count = re.subn(pattern, replacement, text, count=1)
        if count != 1:
            raise SystemExit("WORKER_EVIDENCE_SIGNATURE_NOT_FOUND")
    if "required_workers = _required_workers_for_profile" not in text:
        anchor = '    expected_head = expected_migration_head or os.getenv("EXPECTED_MIGRATION_HEAD")\n\n'
        if text.count(anchor) != 1:
            raise SystemExit("COLLECT_REQUIRED_WORKERS_ANCHOR_NOT_FOUND")
        text = text.replace(anchor, anchor + '    required_workers = _required_workers_for_profile(settings, profile.name.value)\n\n', 1)
    text = text.replace(
        '        "workers": _worker_evidence(db, now=current),\n',
        '        "workers": _worker_evidence(db, now=current, required_workers=required_workers),\n',
    )
    text = re.sub(
        r'        "required_workers": list\(_csv_env\("NEXUS_OSR_REQUIRED_WORKERS", .*?\)\),\n',
        '        "required_workers": list(required_workers),\n',
        text,
        count=1,
    )
    return text


def tests_transform(text: str) -> str:
    text = text.replace(
        '    DEFAULT_REQUIRED_WORKERS,\n',
        '    SHADOW_REQUIRED_WORKERS,\n    WRITE_REQUIRED_WORKERS,\n',
    )
    text = text.replace(
        'def _seed_governed_runtime(db, *, now):\n',
        'def _seed_governed_runtime(db, *, now, include_write_workers: bool = True):\n',
    )
    text = text.replace(
        '    for name in DEFAULT_REQUIRED_WORKERS:\n',
        '    worker_names = list(SHADOW_REQUIRED_WORKERS)\n'
        '    if include_write_workers:\n'
        '        worker_names.extend(WRITE_REQUIRED_WORKERS)\n'
        '    for name in worker_names:\n',
    )
    first_seed = '    _seed_governed_runtime(db_session, now=now)\n    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")\n\n    result = collect_business_readiness(\n'
    if first_seed in text:
        text = text.replace(
            first_seed,
            '    _seed_governed_runtime(db_session, now=now, include_write_workers=False)\n'
            '    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260711_0058")\n\n'
            '    result = collect_business_readiness(\n',
            1,
        )
    text = text.replace(
        'ServiceHeartbeat.service_name == "operations_dispatch_worker"',
        'ServiceHeartbeat.service_name == "webchat_ai_worker"',
        1,
    )
    text = text.replace(
        '            service_name="operations_dispatch_worker",\n',
        '            service_name="webchat_ai_worker",\n',
        1,
    )
    return text


rewrite("backend/app/settings.py", settings_transform)
rewrite("backend/app/services/nexus_osr/business_readiness_service.py", collector_transform)
rewrite("backend/tests/test_nexus_osr_business_readiness.py", tests_transform)
