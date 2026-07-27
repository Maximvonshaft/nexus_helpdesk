from __future__ import annotations

import pytest

from app.settings import get_settings
from app.services import background_job_execution_scope, background_jobs
from app.services.whatsapp_embedded_signup_settings import (
    reset_whatsapp_embedded_signup_settings_cache,
)
from app.services.whatsapp_media_settings import (
    reset_whatsapp_media_settings_cache,
)
from app.services.whatsapp_runtime_settings import (
    reset_whatsapp_runtime_settings_cache,
)


@pytest.fixture(autouse=True)
def isolate_runtime_settings(monkeypatch: pytest.MonkeyPatch):
    """Give every backend test a deterministic non-production runtime baseline.

    Security-contract tests may explicitly override these values within the test.
    Clearing every cached settings authority before and after the test prevents
    production/enforce fixtures from leaking into unrelated suites.
    """

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TENANT_RUNTIME_AUTHORITY_MODE", "shadow")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_EMBEDDED_SIGNUP_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_MEDIA_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_MEDIA_SCANNER", "disabled")
    _reset_settings_caches()
    yield
    _reset_settings_caches()


@pytest.fixture(autouse=True)
def preserve_focused_background_job_test_doubles(
    monkeypatch: pytest.MonkeyPatch,
):
    """Keep non-ORM test doubles focused on attempt-boundary behavior.

    Real SQLAlchemy Sessions always use the canonical execution-scope and lease
    authority. A handful of old unit tests intentionally use a minimal FakeDB to
    exercise rollback/retry semantics; this adapter routes only those doubles
    through their patched claim function and bypasses scope persistence they do
    not model.
    """

    canonical_claim = background_job_execution_scope.claim_executable_background_jobs
    canonical_require = background_job_execution_scope.require_executable_background_job_scope

    def claim(db, *, limit=None, worker_id=None, job_types=None):
        if not hasattr(db, "execute"):
            return background_jobs.claim_pending_jobs(
                db,
                limit=limit,
                worker_id=worker_id,
                job_types=job_types,
            )
        return canonical_claim(
            db,
            limit=limit,
            worker_id=worker_id,
            job_types=job_types,
        )

    def require(db, job):
        if not hasattr(db, "execute"):
            return None
        return canonical_require(db, job)

    monkeypatch.setattr(
        background_job_execution_scope,
        "claim_executable_background_jobs",
        claim,
    )
    monkeypatch.setattr(
        background_job_execution_scope,
        "require_executable_background_job_scope",
        require,
    )


def _reset_settings_caches() -> None:
    get_settings.cache_clear()
    reset_whatsapp_runtime_settings_cache()
    reset_whatsapp_embedded_signup_settings_cache()
    reset_whatsapp_media_settings_cache()
