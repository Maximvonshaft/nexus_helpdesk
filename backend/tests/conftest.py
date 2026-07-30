from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Market, Tenant, User
from app.models_agent_routing import ConversationControl
from app.models_handoff_routing import HandoffRoutingPlan
from app.operator_models import OperatorQueueScopeGrant
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
from app.webchat_models import WebchatHandoffRequest


# SQLite enforces foreign keys during normal test execution. Schema teardown is
# the sole exception: SQLAlchemy cannot topologically drop every table when
# mutually-referencing test tables are populated. Disable the connection-local
# PRAGMA only around metadata teardown, then prove it is restored immediately.
_ORIGINAL_DROP_ALL = Base.metadata.drop_all


def _drop_all_for_test_schema(
    bind: Engine | Connection | None = None,
    tables=None,
    checkfirst: bool = True,
) -> None:
    if bind is None or bind.dialect.name != "sqlite":
        _ORIGINAL_DROP_ALL(bind=bind, tables=tables, checkfirst=checkfirst)
        return

    if isinstance(bind, Connection):
        connection = bind
        owns_connection = False
    else:
        connection = bind.connect()
        owns_connection = True
    try:
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        disabled = int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
        if disabled != 0:
            raise RuntimeError("sqlite_schema_teardown_fk_disable_failed")
        _ORIGINAL_DROP_ALL(
            bind=connection,
            tables=tables,
            checkfirst=checkfirst,
        )
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        enabled = int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
        if enabled != 1:
            raise RuntimeError("sqlite_schema_teardown_fk_restore_failed")
        if connection.in_transaction():
            connection.commit()
    finally:
        if connection.dialect.name == "sqlite":
            if connection.in_transaction():
                connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            if connection.in_transaction():
                connection.commit()
        if owns_connection:
            connection.close()


Base.metadata.drop_all = _drop_all_for_test_schema


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
    "test_webchat_ai_turn_runtime": "pytest-ai-turn-runtime",
    "test_webchat_handoff_control": "pytest",
    "test_webchat_terminal_fallback_delivery": "pytest-terminal-fallback",
    "test_webchat_ai_terminal_outcome_convergence": "pytest-terminal-outcome",
    "test_webchat_voice_api": "pytest-voice",
    "test_webchat_voice_p0_gap_closure": "pytest-voice-p0",
    "test_whatsapp_native_ai_conversation": "pytest-whatsapp-ai",
}

_FORCE_TENANT_KEY_MODULES = {
    "test_channel_workbench_backend_contracts",
    "test_webchat_ai_turn_runtime",
    "test_webchat_terminal_fallback_delivery",
    "test_webchat_ai_terminal_outcome_convergence",
}

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
_MARKET_SCOPED_MODELS = {
    "ChannelAccount",
    "MarketBulletin",
    "OutboundEmailAccount",
    "Team",
    "Ticket",
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
    """Migrate named legacy test factories to current relational authorities."""

    module_name = request.module.__name__.rsplit(".", 1)[-1]
    tenant_key = _LEGACY_FIXTURE_TENANTS.get(module_name)
    if (
        module_name == "test_unified_operator_queue"
        and request.node.name == "test_scope_grant_crud_is_normalized_hashed_and_audited"
    ):
        tenant_key = "tenant.example"
    if tenant_key is None:
        yield
        return

    cache_key = f"nexus.test.tenant:{tenant_key}"
    market_cache_key = f"{cache_key}:default-market"

    def ensure_market(session: Session, tenant_id: int) -> Market:
        market = session.info.get(market_cache_key)
        if market is not None:
            return market
        digest = hashlib.sha256(tenant_key.encode("utf-8")).hexdigest()[:10]
        market = Market(
            tenant_id=int(tenant_id),
            tenant_assignment_source="test_fixture",
            tenant_assignment_version="nexus.test.fixture.v1",
            code=f"T{digest}"[:16],
            name=f"Test Market {digest}",
            country_code="ZZ",
            is_active=True,
        )
        session.add(market)
        session.info[market_cache_key] = market
        return market

    def add_plan_grants(
        session: Session,
        *,
        plan: HandoffRoutingPlan,
        tenant_id: int,
    ) -> None:
        request_row = session.get(WebchatHandoffRequest, int(plan.request_id))
        if request_row is None:
            return
        control = (
            session.query(ConversationControl)
            .filter(
                ConversationControl.conversation_id
                == request_row.conversation_id
            )
            .first()
        )
        if control is None or not control.country_code:
            return
        users = (
            session.query(User)
            .filter(
                User.tenant_id == int(tenant_id),
                User.is_active.is_(True),
            )
            .all()
        )
        pending = {
            (
                int(row.user_id),
                row.tenant_key,
                row.country_code,
                row.channel_key,
                row.queue_key,
            )
            for row in session.new
            if isinstance(row, OperatorQueueScopeGrant)
        }
        for user in users:
            identity = (
                int(user.id),
                control.tenant_key,
                control.country_code,
                control.channel_key,
                plan.owner_queue_key,
            )
            exists = (
                session.query(OperatorQueueScopeGrant.id)
                .filter(
                    OperatorQueueScopeGrant.user_id == user.id,
                    OperatorQueueScopeGrant.tenant_key == control.tenant_key,
                    OperatorQueueScopeGrant.country_code == control.country_code,
                    OperatorQueueScopeGrant.channel_key == control.channel_key,
                    OperatorQueueScopeGrant.queue_key == plan.owner_queue_key,
                )
                .first()
            )
            if exists is not None or identity in pending:
                continue
            session.add(
                OperatorQueueScopeGrant(
                    user_id=int(user.id),
                    tenant_key=control.tenant_key,
                    country_code=control.country_code,
                    channel_key=control.channel_key,
                    queue_key=plan.owner_queue_key,
                    enabled=True,
                    granted_by=int(user.id),
                )
            )
            pending.add(identity)

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

        rows = tuple(session.new) + tuple(session.dirty)
        for row in rows:
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

            if (
                model_name in _MARKET_SCOPED_MODELS
                and not getattr(row, "market_id", None)
                and getattr(row, "market", None) is None
            ):
                row.market = ensure_market(session, int(tenant_id))

            if model_name in _TENANT_KEY_MODELS:
                current = str(getattr(row, "tenant_key", "") or "").strip()
                if (
                    module_name in _FORCE_TENANT_KEY_MODULES
                    or current in {"", "default", "pytest"}
                ):
                    row.tenant_key = tenant_key

            if (
                module_name == "test_webchat_handoff_control"
                and model_name == "ConversationControl"
                and not str(getattr(row, "country_code", "") or "").strip()
            ):
                row.country_code = ensure_market(
                    session,
                    int(tenant_id),
                ).country_code

            if (
                module_name == "test_webchat_handoff_control"
                and model_name == "Ticket"
                and not str(getattr(row, "case_type", "") or "").strip()
            ):
                row.case_type = "tracking_status_inquiry"

            if module_name == "test_channel_workbench_backend_contracts":
                if (
                    model_name == "Ticket"
                    and str(getattr(row, "ticket_no", "") or "").startswith("WEBCALL-")
                    and not str(getattr(row, "case_type", "") or "").strip()
                ):
                    row.case_type = "tracking_inquiry"
                if (
                    model_name == "OperatorQueueScopeGrant"
                    and str(getattr(row, "queue_key", "") or "legacy").strip().lower()
                    == "legacy"
                ):
                    row.queue_key = "customer_support"

        if module_name == "test_webchat_handoff_control":
            for row in tuple(session.new):
                if isinstance(row, HandoffRoutingPlan):
                    add_plan_grants(
                        session,
                        plan=row,
                        tenant_id=int(tenant_id),
                    )

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
