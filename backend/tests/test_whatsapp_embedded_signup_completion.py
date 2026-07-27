from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-embedded-signup.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault(
    "WHATSAPP_ENCRYPTION_KEY",
    Fernet.generate_key().decode("ascii"),
)

from app.api import admin_whatsapp_embedded_signup
from app.db import Base
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import Tenant, User
from app.models_whatsapp import WhatsAppConnection, WhatsAppEmbeddedSignupSession
from app.schemas_whatsapp_signup import EmbeddedSignupCompleteRequest
from app.unit_of_work import managed_session


register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-embedded-signup.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _admin(db_session) -> User:
    tenant = Tenant(
        tenant_key="whatsapp-signup-test",
        display_name="WhatsApp Signup Test",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="sha256:" + "a" * 64,
        username="whatsapp-signup-admin",
        display_name="WhatsApp Signup Admin",
        email="whatsapp-signup-admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _payload(state: str) -> EmbeddedSignupCompleteRequest:
    return EmbeddedSignupCompleteRequest(
        state=state,
        code="authorization-code-12345678",
        business_account_id="1234567890",
        waba_id="2345678901",
        phone_number_id="3456789012",
        display_name="Meta Primary",
        account_id="wa-meta-primary",
        market_id=None,
        priority=100,
    )


def test_completed_signup_retry_returns_existing_connection_without_code_exchange(
    db_session,
    monkeypatch,
):
    admin = _admin(db_session)
    state = "signed-state-" + "x" * 48
    created = admin_whatsapp_embedded_signup.create_whatsapp_connection(
        admin_whatsapp_embedded_signup.WhatsAppConnectionCreate(
            display_name="Meta Existing",
            account_id="wa-meta-existing",
            transport="meta_cloud_api",
            business_account_id="1234567890",
            waba_id="2345678901",
            phone_number_id="3456789012",
            graph_api_version="v99.1",
            access_token="access-token",
            app_secret="app-secret",
            verify_token="verify-token-with-sufficient-length",
        ),
        db_session,
        admin,
    )
    signup = WhatsAppEmbeddedSignupSession(
        id="completed-session",
        tenant_id=admin.tenant_id,
        requested_by=admin.id,
        state_digest=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        status="completed",
        expires_at=admin_whatsapp_embedded_signup.utc_now()
        + admin_whatsapp_embedded_signup.timedelta(minutes=5),
        completed_at=admin_whatsapp_embedded_signup.utc_now(),
        connection_id=created.id,
    )
    db_session.add(signup)
    db_session.commit()

    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "exchange_and_validate_signup",
        lambda **_kwargs: pytest.fail("completed retry must not exchange OAuth code"),
    )
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "start_whatsapp_binding",
        lambda *_args, **_kwargs: pytest.fail("completed retry must not duplicate binding side effects"),
    )

    result = admin_whatsapp_embedded_signup.complete_embedded_signup_session(
        "completed-session",
        EmbeddedSignupCompleteRequest(
            **{
                **_payload(state).model_dump(),
                "account_id": "ignored-on-idempotent-retry",
            }
        ),
        db=db_session,
        current_user=admin,
    )

    assert result.ok is True
    assert result.idempotent is True
    assert result.connection_id == created.id
    assert result.account_id == "wa-meta-existing"
    assert result.binding_state == "started"


def test_binding_failure_preserves_created_connection_and_returns_recovery_state(
    db_session,
    monkeypatch,
):
    admin = _admin(db_session)
    state = "signed-state-" + "y" * 48
    signup = WhatsAppEmbeddedSignupSession(
        id="pending-session",
        tenant_id=admin.tenant_id,
        requested_by=admin.id,
        state_digest=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        status="pending",
        expires_at=admin_whatsapp_embedded_signup.utc_now()
        + admin_whatsapp_embedded_signup.timedelta(minutes=5),
    )
    db_session.add(signup)
    db_session.commit()

    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "require_pending_signup_session",
        lambda db, **_kwargs: db.get(WhatsAppEmbeddedSignupSession, "pending-session"),
    )
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "exchange_and_validate_signup",
        lambda **_kwargs: SimpleNamespace(
            access_token="exchanged-access-token",
            business_account_id="1234567890",
            waba_id="2345678901",
            phone_number_id="3456789012",
        ),
    )
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "get_whatsapp_embedded_signup_settings",
        lambda: SimpleNamespace(
            graph_api_version="v99.1",
            app_secret="embedded-signup-app-secret",
        ),
    )

    def fail_binding(connection_id, db, current_user):
        with managed_session(db):
            connection = db.get(WhatsAppConnection, connection_id)
            assert connection is not None
            connection.desired_state = "binding"
            connection.last_error_code = "meta_waba_subscription_failed"
            connection.last_error_message = "meta_waba_subscription_failed"
            db.flush()
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "meta_waba_subscription_failed",
                "retryable": True,
            },
        )

    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "start_whatsapp_binding",
        fail_binding,
    )

    result = admin_whatsapp_embedded_signup.complete_embedded_signup_session(
        "pending-session",
        _payload(state),
        db=db_session,
        current_user=admin,
    )

    assert result.ok is True
    assert result.idempotent is False
    assert result.binding_state == "attention_required"
    assert result.binding_error_code == "meta_waba_subscription_failed"
    assert result.binding_retryable is True
    connection = db_session.get(WhatsAppConnection, result.connection_id)
    assert connection is not None
    assert connection.channel_account.account_id == "wa-meta-primary"
    persisted_signup = db_session.get(
        WhatsAppEmbeddedSignupSession,
        "pending-session",
    )
    assert persisted_signup is not None
    assert persisted_signup.status == "completed"
    assert persisted_signup.connection_id == result.connection_id
