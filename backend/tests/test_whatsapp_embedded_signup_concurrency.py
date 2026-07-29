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
    "sqlite:////tmp/nexus-whatsapp-embedded-signup-concurrency.db",
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
from app.schemas_whatsapp_signup import EmbeddedSignupCompleteRequest
from app.utils.time import utc_now


register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'embedded-signup-concurrency.db'}",
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
        tenant_key="signup-concurrency",
        display_name="Signup Concurrency",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="sha256:" + "c" * 64,
        username="signup-concurrency-admin",
        display_name="Signup Concurrency Admin",
        email="signup-concurrency@example.test",
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
        display_name="Meta Concurrent",
        account_id="wa-meta-concurrent",
        market_id=None,
        priority=100,
    )


def test_signup_completion_lookup_requests_a_row_lock() -> None:
    sentinel = object()

    class Query:
        locked = False

        def filter(self, *_conditions):
            return self

        def with_for_update(self):
            self.locked = True
            return self

        def first(self):
            return sentinel

    query = Query()
    db = SimpleNamespace(query=lambda _model: query)

    result = admin_whatsapp_embedded_signup._signup_session(
        db,
        session_id="locked-session",
        tenant_id=7,
        requested_by=11,
        for_update=True,
    )

    assert result is sentinel
    assert query.locked is True


def test_losing_completion_request_does_not_overwrite_active_claim(db_session) -> None:
    admin = _admin(db_session)
    state = "signed-state-" + "z" * 48
    row = WhatsAppEmbeddedSignupSession(
        id="claimed-session",
        tenant_id=admin.tenant_id,
        requested_by=admin.id,
        state_digest=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        status="exchanging",
        expires_at=utc_now() + timedelta(minutes=5),
        code_fingerprint=hashlib.sha256(b"winning-code").hexdigest(),
        business_account_id="1234567890",
        waba_id="2345678901",
        phone_number_id="3456789012",
    )
    db_session.add(row)
    db_session.commit()

    with pytest.raises(HTTPException) as captured:
        admin_whatsapp_embedded_signup.complete_embedded_signup_session(
            "claimed-session",
            _payload(state),
            db=db_session,
            current_user=admin,
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == {
        "error_code": "embedded_signup_exchange_in_progress",
        "retryable": True,
    }
    db_session.expire_all()
    persisted = db_session.get(WhatsAppEmbeddedSignupSession, "claimed-session")
    assert persisted is not None
    assert persisted.status == "exchanging"
    assert persisted.last_error_code is None
    assert persisted.code_fingerprint == hashlib.sha256(b"winning-code").hexdigest()
