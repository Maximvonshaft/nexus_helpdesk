from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-webchat-customer-identity.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault(
    "WHATSAPP_ENCRYPTION_KEY",
    Fernet.generate_key().decode("ascii"),
)

from app.db import Base
from app.model_registry import register_all_models
from app.models import Tenant
from app.models_channel_intake import CustomerIdentityBinding
from app.services.conversation_first_service import (
    _bind_webchat_identities,
    _resolve_webchat_customer,
)
from app.services.customer_identity_service import resolve_or_create_customer
from app.utils.normalize import normalize_email, normalize_phone


register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'webchat-customer-identity.db'}",
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


def test_webchat_creation_and_resume_share_the_identity_binding_authority(db_session) -> None:
    tenant = Tenant(
        tenant_key="webchat-identity",
        display_name="WebChat Identity",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()

    customer = _resolve_webchat_customer(
        db_session,
        tenant_id=tenant.id,
        visitor_name="Identity Customer",
        visitor_email=None,
        visitor_phone="+41 79 123 45 67",
        visitor_ref="visitor-initial",
        public_id="webchat-public-1",
    )
    _bind_webchat_identities(
        db_session,
        customer=customer,
        visitor_name="Identity Customer",
        visitor_email="CUSTOMER@EXAMPLE.TEST",
        visitor_phone="+41 79 123 45 67",
        visitor_ref="visitor-resumed",
        public_id="webchat-public-1",
    )
    db_session.flush()

    bindings = {
        (row.identity_type, row.normalized_value): row.customer_id
        for row in db_session.query(CustomerIdentityBinding)
        .filter(CustomerIdentityBinding.tenant_id == tenant.id)
        .all()
    }
    assert bindings[("phone", normalize_phone("+41 79 123 45 67"))] == customer.id
    assert bindings[("email", normalize_email("CUSTOMER@EXAMPLE.TEST"))] == customer.id
    assert bindings[("external_ref", "visitor-initial")] == customer.id
    assert bindings[("external_ref", "visitor-resumed")] == customer.id
    assert bindings[("external_ref", "webchat-public-1")] == customer.id
    assert customer.phone_normalized == normalize_phone("+41 79 123 45 67")
    assert customer.email_normalized == normalize_email("CUSTOMER@EXAMPLE.TEST")

    from_whatsapp = resolve_or_create_customer(
        db_session,
        tenant_id=tenant.id,
        identity_type="phone",
        identity_value="+41791234567",
        display_name="WhatsApp Customer",
        source="whatsapp",
    )
    from_email = resolve_or_create_customer(
        db_session,
        tenant_id=tenant.id,
        identity_type="email",
        identity_value="customer@example.test",
        display_name="Email Customer",
        source="email",
    )
    assert from_whatsapp.id == customer.id
    assert from_email.id == customer.id
