from __future__ import annotations

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.orm import Session

from app.models import Tenant
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


# These suites predate relational Tenant ownership. The bridge is intentionally
# test-only and module-bounded: production code still rejects every unbound or
# cross-Tenant actor/resource. Each listed module is migrated to one explicit,
# deterministic Tenant without changing the business assertions under test.
_LEGACY_FIXTURE_TENANTS = {
    "test_canonical_policy_projection_behavior": "tenant-policy-a",
    "test_channel_workbench_backend_contracts": "pytest-channel-workbench",
    "test_control_tower_contract": "pytest-control-tower",
    "test_email_mailbox_polling_service": "pytest-email-mailbox",
    "test_nexus_osr_tool_execution_service": "pytest-tool-execution",
    "test_operator_product_foundation": "pytest-operator-product",
    "test_operator_queue_current_scopes": "tenant-ops",
    "test_qa_training_contract": "pytest-qa-training",
    "test_unified_operator_queue": "tenant-queue-a",
    "test_webchat_action_idempotency": "pytest-action-idempotency",
    "test_webchat_handoff_control": "pytest",
    "test_webchat_handoff_snapshot_service": "pytest-handoff-snapshot",
    "test_webchat_voice_api": "pytest-voice",
    "test_webchat_voice_p0_gap_closure": "pytest-voice-p0",
}

# Global policy rows such as SLA revisions intentionally have tenant_id=NULL.
# Stamp only concrete business/resource identities that production requires to
# be Tenant-owned; never infer ownership for arbitrary models.
_TENANT_IDENTITY_MODELS = {
    "ChannelAccount",
    "Customer",
    "Market",
    "OperatorTask",
    "Team",
    "Ticket",
    "User",
    "WebchatVoiceSession",
    "WhatsAppConnection",
}
_TENANT_KEY_MODELS = {
    "ConversationControl",
    "OperatorQueueScopeGrant",
    "WebchatConversation",
    "WebchatHandoffRequest",
}


@pytest.fixture(autouse=True)
def isolate_runtime_settings(monkeypatch: pytest.MonkeyPatch):
    """Give every backend test a deterministic non-production runtime baseline."""

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
def migrate_legacy_fixture_tenant_ownership(request: pytest.FixtureRequest):
    """Stamp only named legacy test suites with one deterministic Tenant."""

    module_name = request.module.__name__.rsplit(".", 1)[-1]
    tenant_key = _LEGACY_FIXTURE_TENANTS.get(module_name)
    if tenant_key is None:
        yield
        return

    cache_key = f"nexus.test.tenant:{tenant_key}"

    def before_flush(session: Session, _flush_context, _instances) -> None:
        tenant_id = session.info.get(cache_key)
        if tenant_id is None:
            connection = session.connection()
            tenant_id = connection.execute(
                select(Tenant.id).where(Tenant.tenant_key == tenant_key)
            ).scalar_one_or_none()
            if tenant_id is None:
                result = connection.execute(
                    insert(Tenant).values(
                        tenant_key=tenant_key,
                        display_name=f"Test Tenant {tenant_key}",
                        is_active=True,
                    )
                )
                tenant_id = int(result.inserted_primary_key[0])
            session.info[cache_key] = int(tenant_id)

        for row in tuple(session.new) + tuple(session.dirty):
            model_name = row.__class__.__name__
            if model_name in _TENANT_IDENTITY_MODELS:
                if getattr(row, "tenant_id", None) is None:
                    row.tenant_id = int(tenant_id)
                if hasattr(row, "tenant_assignment_source") and not getattr(
                    row,
                    "tenant_assignment_source",
                    None,
                ):
                    row.tenant_assignment_source = "test_fixture"
                if hasattr(row, "tenant_assignment_version") and not getattr(
                    row,
                    "tenant_assignment_version",
                    None,
                ):
                    row.tenant_assignment_version = "nexus.test.fixture.v1"
            if model_name in _TENANT_KEY_MODELS:
                current = str(getattr(row, "tenant_key", "") or "").strip()
                if current in {"", "default", "pytest"}:
                    row.tenant_key = tenant_key

    event.listen(Session, "before_flush", before_flush)
    try:
        yield
    finally:
        event.remove(Session, "before_flush", before_flush)


@pytest.fixture(autouse=True)
def preserve_focused_background_job_test_doubles(
    monkeypatch: pytest.MonkeyPatch,
):
    """Keep non-ORM test doubles focused on attempt-boundary behavior."""

    canonical_claim = background_job_execution_scope.claim_executable_background_jobs
    canonical_require = background_job_execution_scope.require_executable_background_job_scope

    def claim(db, *, limit=None, worker_id=None, job_types=None):
        if not hasattr(db, "execute"):
            if not hasattr(db, "get"):
                db.get = lambda _model, identity: getattr(db, "rows", {}).get(identity)
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
