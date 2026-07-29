from __future__ import annotations

import hashlib
import os
from datetime import timedelta
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
    "sqlite:////tmp/nexus-whatsapp-embedded-signup-retryability.db",
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
from app.models_whatsapp import WhatsAppEmbeddedSignupSession
from app.models_whatsapp_signup_checkpoint import (
    WhatsAppEmbeddedSignupExchangeCheckpoint,
)
from app.schemas_whatsapp_signup import EmbeddedSignupCompleteRequest
from app.services.whatsapp_embedded_signup import (
    EmbeddedSignupError,
    MetaSignupAssets,
)
from app.utils.time import utc_now


register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'embedded-signup-retryability.db'}",
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
        tenant_key="signup-retryability",
        display_name="Signup Retryability",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="sha256:" + "b" * 64,
        username="signup-retryability-admin",
        display_name="Signup Retryability Admin",
        email="signup-retryability@example.test",
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
        display_name="Meta Retryable",
        account_id="wa-meta-retryable",
        market_id=None,
        priority=100,
    )


def _signup(db_session, admin: User, *, state: str) -> WhatsAppEmbeddedSignupSession:
    row = WhatsAppEmbeddedSignupSession(
        id="retryable-session",
        tenant_id=admin.tenant_id,
        requested_by=admin.id,
        state_digest=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        status="pending",
        expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _require_pending(db, **_kwargs):
    row = db.get(WhatsAppEmbeddedSignupSession, "retryable-session")
    assert row is not None
    if row.status != "pending":
        raise EmbeddedSignupError("embedded_signup_session_not_pending")
    return row


def test_retryable_meta_exchange_failure_restores_pending_for_next_attempt(
    db_session,
    monkeypatch,
):
    admin = _admin(db_session)
    state = "signed-state-" + "r" * 48
    _signup(db_session, admin, state=state)
    observed_statuses: list[str] = []

    def require_pending(db, **_kwargs):
        row = _require_pending(db)
        observed_statuses.append(row.status)
        return row

    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "require_pending_signup_session",
        require_pending,
    )
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "exchange_and_validate_signup",
        lambda **_kwargs: (_ for _ in ()).throw(
            EmbeddedSignupError(
                "embedded_signup_code_exchange_failed",
                retryable=True,
            )
        ),
    )

    for _attempt in range(2):
        with pytest.raises(HTTPException) as captured:
            admin_whatsapp_embedded_signup.complete_embedded_signup_session(
                "retryable-session",
                _payload(state),
                db=db_session,
                current_user=admin,
            )
        assert captured.value.status_code == 503
        db_session.expire_all()
        persisted = db_session.get(
            WhatsAppEmbeddedSignupSession,
            "retryable-session",
        )
        assert persisted is not None
        assert persisted.status == "pending"
        assert persisted.last_error_code == "embedded_signup_code_exchange_failed"

    assert observed_statuses == ["pending", "pending"]


def test_retry_after_exchange_reuses_encrypted_checkpoint_without_code_exchange(
    db_session,
    monkeypatch,
):
    admin = _admin(db_session)
    state = "signed-state-" + "s" * 48
    _signup(db_session, admin, state=state)
    access_token = "post-exchange-token-" + "t" * 32
    observed_resume_tokens: list[str | None] = []

    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "require_pending_signup_session",
        _require_pending,
    )

    def exchange_and_validate(**kwargs):
        resume_token = kwargs.get("access_token")
        observed_resume_tokens.append(resume_token)
        if resume_token is None:
            raise EmbeddedSignupError(
                "embedded_signup_asset_lookup_failed",
                retryable=True,
                resume_access_token=access_token,
            )
        assert resume_token == access_token
        return MetaSignupAssets(
            access_token=resume_token,
            business_account_id="1234567890",
            waba_id="2345678901",
            phone_number_id="3456789012",
            display_phone_number="+15551234567",
            verified_name="Meta Retryable",
        )

    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "exchange_and_validate_signup",
        exchange_and_validate,
    )
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "get_whatsapp_embedded_signup_settings",
        lambda: SimpleNamespace(
            graph_api_version="v24.0",
            app_secret="a" * 48,
        ),
    )
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "create_whatsapp_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=503, detail="stop_after_resume")
        ),
    )

    with pytest.raises(HTTPException) as first:
        admin_whatsapp_embedded_signup.complete_embedded_signup_session(
            "retryable-session",
            _payload(state),
            db=db_session,
            current_user=admin,
        )
    assert first.value.status_code == 503
    db_session.expire_all()
    checkpoint = db_session.get(
        WhatsAppEmbeddedSignupExchangeCheckpoint,
        "retryable-session",
    )
    assert checkpoint is not None
    assert access_token not in checkpoint.encrypted_access_token
    persisted = db_session.get(
        WhatsAppEmbeddedSignupSession,
        "retryable-session",
    )
    assert persisted is not None
    assert persisted.status == "pending"

    with pytest.raises(HTTPException) as second:
        admin_whatsapp_embedded_signup.complete_embedded_signup_session(
            "retryable-session",
            _payload(state),
            db=db_session,
            current_user=admin,
        )
    assert second.value.status_code == 503
    assert observed_resume_tokens == [None, access_token]
    db_session.expire_all()
    assert (
        db_session.get(
            WhatsAppEmbeddedSignupExchangeCheckpoint,
            "retryable-session",
        )
        is None
    )


def test_non_retryable_meta_exchange_failure_remains_terminal(
    db_session,
    monkeypatch,
):
    admin = _admin(db_session)
    state = "signed-state-" + "n" * 48
    _signup(db_session, admin, state=state)
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "require_pending_signup_session",
        _require_pending,
    )
    monkeypatch.setattr(
        admin_whatsapp_embedded_signup,
        "exchange_and_validate_signup",
        lambda **_kwargs: (_ for _ in ()).throw(
            EmbeddedSignupError(
                "embedded_signup_token_invalid",
                retryable=False,
            )
        ),
    )

    with pytest.raises(HTTPException) as captured:
        admin_whatsapp_embedded_signup.complete_embedded_signup_session(
            "retryable-session",
            _payload(state),
            db=db_session,
            current_user=admin,
        )

    assert captured.value.status_code == 409
    db_session.expire_all()
    persisted = db_session.get(
        WhatsAppEmbeddedSignupSession,
        "retryable-session",
    )
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.last_error_code == "embedded_signup_token_invalid"
