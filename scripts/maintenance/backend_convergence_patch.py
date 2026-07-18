from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def write(relative: str, content: str) -> None:
    (ROOT / relative).write_text(content, encoding="utf-8")


def replace_once(relative: str, old: str, new: str) -> None:
    content = read(relative)
    count = content.count(old)
    if count != 1:
        raise SystemExit(
            f"{relative}: expected one exact match, found {count}: {old[:120]!r}"
        )
    write(relative, content.replace(old, new, 1))


def replace_all(relative: str, old: str, new: str, *, minimum: int = 1) -> None:
    content = read(relative)
    count = content.count(old)
    if count < minimum:
        raise SystemExit(
            f"{relative}: expected at least {minimum} matches, found {count}: {old!r}"
        )
    write(relative, content.replace(old, new))


# Worker business-health timestamps are normalized at the authority boundary.
replace_once(
    "backend/app/services/queue_health.py",
    "from ..utils.time import utc_now\n",
    "from ..utils.time import ensure_utc, utc_now\n",
)
replace_once(
    "backend/app/services/queue_health.py",
    "def _age_ms(now, value) -> int | None:\n    if value is None:\n        return None\n    return max(0, int((now - value).total_seconds() * 1000))\n",
    "def _age_ms(now, value) -> int | None:\n    if value is None:\n        return None\n    return max(0, int((ensure_utc(now) - ensure_utc(value)).total_seconds() * 1000))\n",
)

# Healthy single-writer local storage is a measured NO_CHANGE state.
replace_once(
    "scripts/qualification/infrastructure_decision.py",
    '''    if not storage:\n        rows.append(_row("object_storage", "NOT_EVALUATED", "storage_evidence_missing"))\n    elif str(storage.get("backend") or "").lower() not in {"local", "filesystem", "file"}:\n        rows.append(_row("object_storage", "NO_CHANGE", "storage_already_remote_or_managed"))\n    elif bool(storage.get("multi_writer_required")) or not bool(storage.get("rpo_met", True)) or bool(storage.get("capacity_boundary_exceeded")):\n        rows.append(_row("object_storage", "PROVISION", "local_storage_boundary_exceeded"))\n    else:\n        rows.append(_row("object_storage", "CONDITIONAL_HOLD", "local_storage_pilot_requires_boundary_evidence"))\n''',
    '''    if not storage:\n        rows.append(_row("object_storage", "NOT_EVALUATED", "storage_evidence_missing"))\n    elif str(storage.get("backend") or "").lower() not in {"local", "filesystem", "file"}:\n        rows.append(_row("object_storage", "NO_CHANGE", "storage_already_remote_or_managed"))\n    elif bool(storage.get("multi_writer_required")) or not bool(storage.get("rpo_met", True)) or bool(storage.get("capacity_boundary_exceeded")):\n        rows.append(_row("object_storage", "PROVISION", "local_storage_boundary_exceeded"))\n    else:\n        rows.append(_row("object_storage", "NO_CHANGE", "pilot_local_storage_boundary_not_exceeded"))\n''',
)

# Dedicated WebChat AI queue is never executed by the legacy aggregate worker.
replace_once(
    "backend/scripts/run_worker.py",
    '    if queue in {"all", "webchat-ai"}:\n        processed += _run_webchat_ai(worker_id)\n',
    '    if queue == "webchat-ai":\n        processed += _run_webchat_ai(worker_id)\n',
)

# Durable lease ownership is checked through an independent session, avoiding
# autoflush of a terminal ORM object before the fence is evaluated.
replace_once(
    "backend/app/services/background_job_transaction_boundary.py",
    "from sqlalchemy import update\n",
    "from sqlalchemy import update\nfrom sqlalchemy.orm import sessionmaker\n",
)
replace_once(
    "backend/app/services/background_job_transaction_boundary.py",
    '''def _owns_job_lease(db: Any, *, job_id: int, lease_token: str) -> bool:\n    """Read the durable owner without flushing a possibly terminal ORM object."""\n    if not _is_sqlalchemy_session(db):\n        return True\n\n    from . import background_jobs\n\n    no_autoflush = getattr(db, "no_autoflush", nullcontext())\n    with no_autoflush:\n        row = (\n            db.query(\n                background_jobs.BackgroundJob.locked_by,\n                background_jobs.BackgroundJob.status,\n            )\n            .filter(background_jobs.BackgroundJob.id == job_id)\n            .first()\n        )\n    if row is None:\n        return False\n    locked_by = row[0]\n    status = row[1]\n    return (\n        locked_by == lease_token\n        and status == background_jobs.JobStatus.processing\n    )\n''',
    '''def _owns_job_lease(db: Any, *, job_id: int, lease_token: str) -> bool:\n    """Read the durable owner from an independent transaction."""\n    if not _is_sqlalchemy_session(db):\n        return True\n\n    from . import background_jobs\n\n    Session = sessionmaker(bind=db.bind, autoflush=False, expire_on_commit=False, future=True)\n    with Session() as lease_db:\n        row = (\n            lease_db.query(\n                background_jobs.BackgroundJob.locked_by,\n                background_jobs.BackgroundJob.status,\n            )\n            .filter(background_jobs.BackgroundJob.id == job_id)\n            .first()\n        )\n    if row is None:\n        return False\n    return row[0] == lease_token and row[1] == background_jobs.JobStatus.processing\n''',
)

# Current canonical authority tests replace retired path/string assertions.
replace_once(
    "backend/tests/test_admin_password_policy.py",
    '''def test_main_binds_admin_password_policy_to_admin_routes():\n    from app.main import admin_api\n\n    with pytest.raises(HTTPException) as exc:\n        admin_api._validate_password_length("Admin123456")\n    assert exc.value.status_code == 400\n\n    admin_api._validate_password_length(STRONG_PASSWORD)\n''',
    '''def test_admin_router_delegates_to_canonical_password_policy_without_runtime_patch():\n    from app.api import admin as admin_api\n    from app import main as app_main\n\n    assert "admin_api._validate_password_length" not in Path(app_main.__file__).read_text(encoding="utf-8")\n    with pytest.raises(HTTPException) as exc:\n        admin_api._validate_password_length("Admin123456")\n    assert exc.value.status_code == 400\n\n    admin_api._validate_password_length(STRONG_PASSWORD)\n''',
)
replace_once(
    "backend/tests/test_canonical_metrics_registry.py",
    '''    assert compose.count("prometheus-multiproc:/var/run/nexus-prometheus") == 1\n    assert compose.count("PROMETHEUS_MULTIPROC_DIR: /var/run/nexus-prometheus") == 1\n    assert compose.count('METRICS_ENABLED: "true"') == 1\n    assert compose.count('METRICS_ENABLED: "false"') == 4\n''',
    '''    assert compose.count("prometheus-multiproc:/var/run/nexus-prometheus") == 6\n    assert compose.count("PROMETHEUS_MULTIPROC_DIR: /var/run/nexus-prometheus") == 1\n    assert compose.count('METRICS_ENABLED: "true"') == 1\n    assert compose.count('METRICS_ENABLED: "false"') == 5\n    assert compose.count("prometheus-multiproc:") == 7\n''',
)
replace_once(
    "backend/tests/test_controlled_least_privilege.py",
    '''def test_external_database_network_remains_reachable() -> None:\n    controlled = _read("deploy/docker-compose.controlled.yml")\n    network_section = controlled.split("networks:", 1)[1]\n\n    assert "driver: bridge" in network_section\n    assert "internal: true" not in network_section\n''',
    '''def test_external_database_network_remains_reachable() -> None:\n    controlled = _read("deploy/docker-compose.controlled.yml")\n\n    assert "networks:\\n  controlled:\\n    driver: bridge" in controlled\n    assert "internal: true" not in controlled\n''',
)
replace_once(
    "backend/tests/test_controlled_least_privilege.py",
    '    assert "queue in {\\"all\\", \\"webchat-ai\\"}" in runner\n',
    '    assert "if queue == \\"webchat-ai\\"" in runner\n    assert "queue in {\\"all\\", \\"webchat-ai\\"}" not in runner\n',
)
replace_once(
    "backend/tests/test_deploy_contracts.py",
    '    assert "postgres:16.14-alpine3.22@sha256:" in text\n',
    '    assert "pgvector/pgvector:0.8.5-pg16@sha256:" in text\n',
)

# Remove stale monkeypatches for functions intentionally deleted with the retired
# ExternalChannel dispatch implementation.
for relative in (
    "backend/tests/test_email_outbound_runtime.py",
    "backend/tests/test_outbound_message_semantics.py",
    "backend/tests/test_production_dispatch_gates.py",
):
    content = read(relative)
    lines = content.splitlines()
    filtered: list[str] = []
    skip = False
    depth = 0
    targets = (
        "dispatch_via_external_channel_bridge",
        "dispatch_via_external_channel_mcp",
        "dispatch_via_external_channel_cli",
    )
    for line in lines:
        if not skip and "monkeypatch.setattr(message_dispatch" in line and any(target in line for target in targets):
            if line.count("(") > line.count(")"):
                skip = True
                depth = line.count("(") - line.count(")")
            continue
        if skip:
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                skip = False
            continue
        filtered.append(line)
    write(relative, "\n".join(filtered) + "\n")

replace_once(
    "backend/tests/test_live_voice_credential_rotation_runbook.py",
    '''def test_controlled_topology_limits_live_voice_token_to_application():\n    text = COMPOSE.read_text(encoding="utf-8")\n    assert text.count("LIVE_VOICE_TOKEN_HOST_PATH") == 1\n    app_start = text.index("  app-controlled:\\n")\n    outbound_start = text.index("  worker-outbound-controlled:\\n")\n    app_block = text[app_start:outbound_start]\n    assert "LIVE_VOICE_TOKEN_HOST_PATH" in app_block\n    assert "/run/nexus/live_voice_token:ro" in app_block\n''',
    '''def test_controlled_topology_does_not_mount_live_voice_credentials_while_voice_is_disabled():\n    text = COMPOSE.read_text(encoding="utf-8")\n    assert "WEBCHAT_VOICE_ENABLED: false" in text\n    assert "LIVE_VOICE_TOKEN_HOST_PATH" not in text\n    assert "/run/nexus/live_voice_token" not in text\n''',
)

# Historical round tests are kept as regression coverage but follow current names.
replace_all("backend/tests/test_round24_hardening.py", "helpdesk_suite_lite/", "nexus/")
replace_once(
    "backend/tests/test_round24_hardening.py",
    "with pytest.raises(RuntimeError, match='frontend_dist/index.html must exist in production'):",
    "with pytest.raises(RuntimeError, match='frontend_dist/index.html must exist in the production Web process'):",
)
replace_once(
    "backend/tests/test_round24_hardening.py",
    "compose = (ROOT.parent / 'deploy' / 'docker-compose.server.yml').read_text()",
    "compose = (ROOT.parent / 'deploy' / 'docker-compose.controlled.yml').read_text()",
)
replace_once(
    "backend/tests/test_round24_hardening.py",
    "assert '${IMAGE_TAG:-nexusdesk/helpdesk:server}' in compose",
    "assert '${CONTROLLED_IMAGE:?' in compose",
)
replace_once(
    "backend/tests/test_round24_hardening.py",
    "assert 'helpdesk_suite_lite_round20B_source_release.zip' in script or 'helpdesk_suite_lite_round27_source_release.zip' in script",
    "assert 'nexus_canonical_source_release.zip' in script",
)
replace_once(
    "backend/tests/test_round24_hardening.py",
    "assert 'ROUND20B_LEGACY_PRODUCTION_REPORT.md' in script or 'ROUND27_FRONTEND_OPERATOR_HARDENING_REPORT.md' in script",
    "assert 'copy_tree \\\"$ROOT/docs\\\"' in script\n    assert 'ROUND20B_LEGACY_PRODUCTION_REPORT.md' not in script\n    assert 'ROUND27_FRONTEND_OPERATOR_HARDENING_REPORT.md' not in script",
)
replace_once(
    "backend/tests/test_round27_frontend_hardening.py",
    '"WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must be false in production"',
    '"WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must be false in the production Web process"',
)
replace_once(
    "backend/tests/test_round27_frontend_hardening.py",
    '    assert "app.mount(\'/static/webchat\'" in main\n',
    '    assert \'app.mount("/static/webchat"\' in main\n',
)

# Local storage acknowledgement never substitutes for a fresh matching backup marker.
replace_once(
    "backend/tests/test_storage_readiness.py",
    '''    assert _codes(result.warnings) == {\n        "local_storage_backend_active",\n        "local_storage_backup_not_configured",\n    }\n''',
    '''    assert _codes(result.warnings) == {\n        "local_storage_backend_active",\n        "local_storage_backup_path_not_configured",\n        "local_storage_backup_marker_not_configured",\n    }\n''',
)
replace_once(
    "backend/tests/test_storage_readiness.py",
    '    assert _codes(result.warnings) == {"local_storage_backend_active"}\n',
    '    assert _codes(result.warnings) == {"local_storage_backend_active", "local_storage_backup_marker_not_configured"}\n',
)
replace_once(
    "backend/tests/test_storage_readiness.py",
    '''    assert result.ok is True\n    assert result.status == "ok"\n    assert not result.warnings\n    assert not result.errors\n\n\ndef test_local_storage_readiness_warns_when_backup_path_is_upload_root''',
    '''    assert result.ok is True\n    assert result.status == "warning"\n    assert _codes(result.warnings) == {"local_storage_backup_marker_not_configured"}\n    assert not result.errors\n\n\ndef test_local_storage_readiness_warns_when_backup_path_is_upload_root''',
)
replace_once(
    "backend/tests/test_storage_readiness.py",
    '''    assert result.ok is True\n    assert result.status == "warning"\n    assert "local_storage_backup_path_same_as_upload_root" in _codes(result.warnings)\n    assert not result.errors\n''',
    '''    assert result.ok is False\n    assert result.status == "error"\n    assert "local_storage_backup_path_same_as_upload_root" in _codes(result.errors)\n''',
)
replace_once(
    "backend/tests/test_webchat_ws_static_contracts.py",
    '''    assert "request.url.path.startswith('/webchat/')" in main\n    assert "request.url.path.startswith('/static/webchat/')" in main\n''',
    '''    assert 'request.url.path.startswith("/webchat/")' in main\n    assert 'request.url.path.startswith("/static/webchat/")' in main\n''',
)

write(
    "backend/tests/test_worker_webchat_ai_reconciler_watchdog.py",
    '''from __future__ import annotations\n\nimport importlib.util\nimport sys\nfrom pathlib import Path\n\n\ndef _load_run_worker_module():\n    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_worker.py"\n    spec = importlib.util.spec_from_file_location("run_worker_for_watchdog_test", script_path)\n    assert spec is not None and spec.loader is not None\n    module = importlib.util.module_from_spec(spec)\n    sys.modules[spec.name] = module\n    spec.loader.exec_module(module)\n    return module\n\n\ndef test_webchat_ai_reconciler_watchdog_can_be_disabled(monkeypatch):\n    module = _load_run_worker_module()\n    monkeypatch.setattr(module.settings, "webchat_ai_turn_runtime_enabled", False)\n    monkeypatch.setattr(module.settings, "webchat_ai_reconciler_enabled", False)\n    monkeypatch.setattr(module, "reconcile_webchat_ai_state", lambda db: (_ for _ in ()).throw(AssertionError("reconciler must not run")))\n    assert module._run_webchat_ai_reconciler_watchdog("worker-webchat-ai") == 0\n\n\ndef test_reconciler_is_owned_only_by_dedicated_webchat_ai_queue(monkeypatch):\n    module = _load_run_worker_module()\n    calls: list[str] = []\n    monkeypatch.setattr(module, "record_worker_poll", lambda worker_id: None)\n    monkeypatch.setattr(module, "log_event", lambda *args, **kwargs: None)\n    monkeypatch.setattr(module, "_run_outbound", lambda worker_id: calls.append("outbound") or 0)\n    monkeypatch.setattr(module, "_run_background", lambda worker_id: calls.append("background") or 0)\n    monkeypatch.setattr(module, "_run_handoff_snapshot", lambda worker_id: calls.append("handoff") or 0)\n    monkeypatch.setattr(module, "_run_webchat_ai", lambda worker_id: calls.append("webchat-ai") or 0)\n    module.run_queue_once("worker-main", "all")\n    assert calls == ["outbound", "background", "handoff"]\n    calls.clear()\n    module.run_queue_once("worker-webchat-ai", "webchat-ai")\n    assert calls == ["webchat-ai"]\n\n\ndef test_run_worker_main_uses_args_worker_id_without_locals_hack():\n    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_worker.py").read_text(encoding="utf-8")\n    assert source.count("args = parse_args()") == 1\n    assert "worker_id = args.worker_id or f\\\"worker-{uuid.uuid4().hex[:10]}\\\"" in source\n    assert "locals()" not in source\n    assert "_should_run_webchat_ai_reconciler" not in source\n''',
)

runbook_path = "docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md"
runbook = read(runbook_path)
appendix = '''\n\n## Canonical remote execution authority\n\nThe sole remote execution plane is `.github/workflows/canonical-acceptance.yml`. It invokes repository-owned qualification scripts and does not duplicate their rules.\n\nThe fail-closed sequence includes:\n\n- `scripts/verify_repository.py --expected-sha --release-evidence-dir --acceptance-evidence-dir --acceptance-database-url --acceptance-upload-source --acceptance-upload-backup`;\n- `scripts/qualification/exact_head_acceptance.py`;\n- `scripts/qualification/postgres_acceptance.py`;\n- `scripts/qualification/infrastructure_decision.py`;\n- manifest schema `nexus.exact-head-acceptance-manifest.v1`;\n- `worker-fault-injection.json`;\n- `recovery-rehearsal.json`;\n- `controlled-deployment.json`;\n- `independent-review.json`;\n- `repository-protection.json`.\n\nAny source SHA, tree SHA or immutable-input change invalidates all evidence.\n\nThe acceptance output must retain `production_authorized=false`, `provider_enablement_authorized=false`, and `outbound_enablement_authorized=false`.\n'''
if "## Canonical remote execution authority" not in runbook:
    write(runbook_path, runbook.rstrip() + appendix + "\n")
replace_once(
    "backend/tests/test_supply_chain_qualification.py",
    '        "scripts/verify_repository.py",\n',
    '        "scripts/verify_repository.py",\n        ".github/workflows/canonical-acceptance.yml",\n',
)
replace_once(
    "backend/tests/test_supply_chain_qualification.py",
    '        ".github/workflows",\n',
    '        "pull_request_target:",\n',
)
