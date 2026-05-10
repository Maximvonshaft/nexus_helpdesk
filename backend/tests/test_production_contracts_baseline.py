from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def _read(path: str) -> str:
    return (PROJECT / path).read_text()


def test_lite_workflow_status_change_uses_capability_override_session():
    lite_service = _read('backend/app/services/lite_service.py')

    assert 'ensure_can_change_status(current_user, ticket, internal, db)' in lite_service
    assert 'ensure_can_change_status(current_user, ticket, internal)\n' not in lite_service


def test_remote_storage_upload_preserves_null_file_path():
    file_service = _read('backend/app/services/file_service.py')

    assert 'file_path=str(stored.absolute_path) if stored.absolute_path is not None else None' in file_service
    assert 'file_path=str(stored.absolute_path),' not in file_service


def test_healthz_and_readyz_do_not_expose_runtime_identity():
    main = _read('backend/app/main.py')

    healthz_block = main.split("@app.get('/healthz')", 1)[1].split("@app.get('/metrics')", 1)[0]
    readyz_block = main.split("@app.get('/readyz')", 1)[1].split("# Semantic overrides", 1)[0]

    assert "return {'status': 'ok'}" in healthz_block
    for leaked_field in ['git_sha', 'image_tag', 'build_time', 'frontend_build_sha']:
        assert leaked_field not in healthz_block
        assert leaked_field not in readyz_block
    assert '**_runtime_identity()' not in healthz_block
    assert '**_runtime_identity()' not in readyz_block


def test_request_failure_logs_are_sanitized():
    main = _read('backend/app/main.py')

    assert 'def _sanitize_exception(exc: Exception)' in main
    assert "return {'error_type': exc.__class__.__name__}" in main
    assert "error=str(exc)" not in main
    assert '**_sanitize_exception(exc)' in main


def test_csp_connect_src_is_configurable_without_wildcarding_production():
    main = _read('backend/app/main.py')

    assert "os.getenv('CSP_CONNECT_SRC', \"'self'\")" in main
    assert "if settings.app_env == 'production' and '*' in tokens:" in main
    assert 'return "\'self\'"' in main
    assert 'f"connect-src {_csp_connect_src()}; "' in main
    assert "default-src 'self'" in main


def test_spa_fallback_has_explicit_path_traversal_guard():
    main = _read('backend/app/main.py')

    assert 'frontend_root_resolved = frontend_dir.resolve()' in main
    assert 'file_path = (frontend_dir / full_path).resolve()' in main
    assert 'file_path.relative_to(frontend_root_resolved)' in main
    assert "return JSONResponse(status_code=404, content={\"detail\": \"Not Found\"})" in main


def test_backend_ci_has_real_postgres_migration_runtime_gate():
    backend_ci = _read('.github/workflows/backend-ci.yml')

    assert 'postgres-migration-runtime:' in backend_ci
    assert 'image: postgres:16' in backend_ci
    assert 'alembic upgrade head' in backend_ci
    assert 'alembic current' in backend_ci
    assert 'python -m uvicorn app.main:app' in backend_ci
    assert 'curl -fsS http://127.0.0.1:18080/healthz' in backend_ci
    assert 'curl -fsS http://127.0.0.1:18080/readyz' in backend_ci
