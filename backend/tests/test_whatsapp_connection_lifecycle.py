from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus-whatsapp-lifecycle.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault(
    "WHATSAPP_ENCRYPTION_KEY",
    Fernet.generate_key().decode("ascii"),
)

from app.api import admin_whatsapp
from app.db import Base
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import Tenant, User
from app.models_whatsapp import WhatsAppConnection
from app.schemas_whatsapp import WhatsAppConnectionCreate
from app.services.secret_crypto import SecretCryptoService
from app.services.whatsapp_connection_service import (
    WhatsAppActivationError,
    apply_observed_snapshot,
    assert_connection_can_activate,
    record_verification_evidence,
)
from app.services.whatsapp_transport_registry import (
    BAILEYS_SIDECAR_TRANSPORT,
    META_CLOUD_API_TRANSPORT,
)

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-lifecycle.db'}",
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
        tenant_key="whatsapp-test",
        display_name="WhatsApp Test",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="sha256:" + "a" * 64,
        username="whatsapp-admin",
        display_name="WhatsApp Admin",
        email="whatsapp-admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_baileys_create_produces_one_inactive_channel_account_and_binding_state(
    db_session,
):
    admin = _admin(db_session)
    result = admin_whatsapp.create_whatsapp_connection(
        WhatsAppConnectionCreate(
            display_name="Baileys Main",
            account_id="wa-baileys-main",
            transport=BAILEYS_SIDECAR_TRANSPORT,
            sidecar_session_key="wa-baileys-main",
        ),
        db=db_session,
        current_user=admin,
    )

    row = db_session.get(WhatsAppConnection, result.id)
    assert row is not None
    assert row.channel_account.provider == "whatsapp"
    assert row.channel_account.is_active is False
    assert row.channel_account.tenant_id == admin.tenant_id
    assert row.tenant_id == admin.tenant_id
    assert row.transport == BAILEYS_SIDECAR_TRANSPORT
    assert row.desired_state == "disabled"
    assert row.sidecar_session_key == "wa-baileys-main"


def test_meta_credentials_are_encrypted_and_never_returned(db_session):
    admin = _admin(db_session)
    result = admin_whatsapp.create_whatsapp_connection(
        WhatsAppConnectionCreate(
            display_name="Meta Main",
            account_id="wa-meta-main",
            transport=META_CLOUD_API_TRANSPORT,
            waba_id="waba-1",
            phone_number_id="phone-1",
            graph_api_version="v99.1",
            access_token="meta-access-token",
            app_secret="meta-app-secret",
            verify_token="meta-webhook-verify-token",
        ),
        db=db_session,
        current_user=admin,
    )

    row = db_session.get(WhatsAppConnection, result.id)
    assert row is not None
    assert row.access_token_encrypted != "meta-access-token"
    assert row.app_secret_encrypted != "meta-app-secret"
    assert row.verify_token_encrypted != "meta-webhook-verify-token"
    crypto = SecretCryptoService.whatsapp()
    assert crypto.decrypt(row.access_token_encrypted) == "meta-access-token"
    assert result.access_token_configured is True
    assert result.app_secret_configured is True
    assert result.verify_token_configured is True
    assert "access_token" not in result.model_dump()
    assert "app_secret" not in result.model_dump()
    assert "verify_token" not in result.model_dump()


def test_binding_is_a_runtime_state_but_never_a_customer_sendable_state(
    db_session,
    monkeypatch,
):
    admin = _admin(db_session)
    created = admin_whatsapp.create_whatsapp_connection(
        WhatsAppConnectionCreate(
            display_name="Baileys Binding",
            account_id="wa-binding",
            transport=BAILEYS_SIDECAR_TRANSPORT,
            sidecar_session_key="wa-binding",
        ),
        db=db_session,
        current_user=admin,
    )

    monkeypatch.setattr(
        admin_whatsapp,
        "call_baileys_account_action",
        lambda connection, action, *, method: SimpleNamespace(
            generation=1,
            qr_status="pending",
            qr_data_url="data:image/png;base64,test",
            as_dict=lambda: {
                "status": "qr_pending",
                "authentication_state": "pending",
                "listener_state": "starting",
                "generation": 1,
                "qr_expires_at": "2026-07-27T12:00:00Z",
            },
        ),
    )

    admin_whatsapp.start_whatsapp_binding(
        created.id,
        db=db_session,
        current_user=admin,
    )
    row = db_session.get(WhatsAppConnection, created.id)
    assert row is not None
    assert row.desired_state == "binding"
    assert row.channel_account.is_active is False
    assert row.verification_state == "pending"
    with pytest.raises(WhatsAppActivationError, match="verification_required"):
        assert_connection_can_activate(row)


def test_both_transports_use_the_same_observed_and_dual_verification_gate():
    for transport in (BAILEYS_SIDECAR_TRANSPORT, META_CLOUD_API_TRANSPORT):
        connection = WhatsAppConnection(
            tenant_id=1,
            channel_account_id=1,
            transport=transport,
            desired_state="binding",
            desired_generation=3,
            observed_generation=0,
            verification_state="pending",
            authentication_state="pending",
            listener_state="starting",
            observed_state="connecting",
            sidecar_session_key=("wa-test" if transport == BAILEYS_SIDECAR_TRANSPORT else None),
            waba_id=("waba" if transport == META_CLOUD_API_TRANSPORT else None),
            phone_number_id=("phone" if transport == META_CLOUD_API_TRANSPORT else None),
            graph_api_version=("v99.1" if transport == META_CLOUD_API_TRANSPORT else None),
            access_token_encrypted=("encrypted" if transport == META_CLOUD_API_TRANSPORT else None),
            app_secret_encrypted=("encrypted" if transport == META_CLOUD_API_TRANSPORT else None),
            verify_token_encrypted=("encrypted" if transport == META_CLOUD_API_TRANSPORT else None),
        )
        apply_observed_snapshot(
            connection,
            {
                "status": "connected",
                "authentication_state": "linked",
                "listener_state": "active",
                "generation": 3,
            },
        )
        record_verification_evidence(connection, inbound=True)
        assert connection.verification_state == "inbound_verified"
        with pytest.raises(WhatsAppActivationError, match="verification_required"):
            assert_connection_can_activate(connection)
        record_verification_evidence(connection, outbound=True)
        assert connection.verification_state == "verified"
        assert_connection_can_activate(connection)
