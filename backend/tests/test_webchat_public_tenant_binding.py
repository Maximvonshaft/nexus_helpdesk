from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.db import Base
from app.model_registry import register_all_models
from app.models import Customer, Tenant, Ticket
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.services import webchat_rate_limit
from app.services import webchat_service
from app.services.webchat_service import create_or_resume_conversation
from app.services.webchat_tenant_binding import (
    normalize_public_origin,
    resolve_public_webchat_scope,
)
from app.webchat_models import WebchatConversation


def _request(origin: str | None = "https://tenant-a.example") -> Request:
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/webchat/init",
            "raw_path": b"/api/webchat/init",
            "query_string": b"",
            "headers": headers,
            "client": ("203.0.113.10", 50000),
            "server": ("testserver", 443),
        }
    )


@pytest.fixture()
def db():
    register_all_models()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _binding(db, *, origin: str = "https://tenant-a.example", tenant: str = "tenant-a", channel: str = "webchat"):
    row = WebchatPublicOriginBinding(
        normalized_origin=origin,
        tenant_key=tenant,
        channel_key=channel,
        display_name="Tenant A widget",
        is_active=True,
    )
    db.add(row)
    db.commit()
    return row



def _tenant(db, *, tenant_key: str = "tenant-a", active: bool = True) -> Tenant:
    row = Tenant(tenant_key=tenant_key, display_name=f"Tenant {tenant_key}", is_active=active)
    db.add(row)
    db.commit()
    return row


def _payload(**overrides):
    values = {
        "tenant_key": "default",
        "channel_key": "default",
        "conversation_id": None,
        "visitor_token": None,
        "visitor_name": "Tenant Visitor",
        "visitor_email": "visitor@invalid.test",
        "visitor_phone": None,
        "visitor_ref": None,
        "origin": None,
        "page_url": "https://tenant-a.example/help",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_production_requires_server_binding(db) -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_public_webchat_scope(
            db,
            request=_request(),
            requested_tenant_key="tenant-a",
            requested_channel_key="webchat",
            app_env="production",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_public_binding_required"


def test_binding_overrides_legacy_default_scope(db) -> None:
    row = _binding(db)
    scope = resolve_public_webchat_scope(
        db,
        request=_request(),
        requested_tenant_key="default",
        requested_channel_key="default",
        app_env="production",
    )
    assert scope.tenant_key == "tenant-a"
    assert scope.channel_key == "webchat"
    assert scope.normalized_origin == "https://tenant-a.example"
    assert scope.binding_id == row.id
    assert scope.authority == "server_origin_binding"


def test_explicit_forged_tenant_fails_closed(db) -> None:
    _binding(db)
    with pytest.raises(HTTPException) as exc:
        resolve_public_webchat_scope(
            db,
            request=_request(),
            requested_tenant_key="tenant-b",
            requested_channel_key="webchat",
            app_env="production",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_public_scope_mismatch"


def test_resume_scope_must_match_persisted_conversation(db) -> None:
    _binding(db, tenant="tenant-b")
    conversation = WebchatConversation(
        public_id="wc_existing",
        visitor_token_hash=hashlib.sha256(b"token").hexdigest(),
        tenant_key="tenant-a",
        channel_key="webchat",
        origin="https://tenant-a.example",
        status="open",
    )
    db.add(conversation)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        resolve_public_webchat_scope(
            db,
            request=_request(),
            requested_tenant_key="default",
            requested_channel_key="default",
            conversation_id="wc_existing",
            app_env="production",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_public_scope_mismatch"


def test_verified_scope_is_applied_at_final_orm_boundary(db) -> None:
    _binding(db)
    resolve_public_webchat_scope(
        db,
        request=_request(),
        requested_tenant_key="default",
        requested_channel_key="default",
        app_env="production",
    )
    conversation = WebchatConversation(
        public_id="wc_new",
        visitor_token_hash=hashlib.sha256(b"token").hexdigest(),
        tenant_key="attacker-selected",
        channel_key="attacker-channel",
        origin="https://spoofed.example",
        status="open",
    )
    db.add(conversation)
    db.flush()

    assert conversation.tenant_key == "tenant-a"
    assert conversation.channel_key == "webchat"
    assert conversation.origin == "https://tenant-a.example"


def test_rate_limit_bucket_uses_verified_server_tenant(db, monkeypatch: pytest.MonkeyPatch) -> None:
    _binding(db)
    captured: dict[str, str] = {}

    def capture_bucket(*, request, tenant_key, conversation_id):
        captured["tenant_key"] = tenant_key
        return "bounded-bucket"

    monkeypatch.setattr(webchat_rate_limit, "_bucket_key", capture_bucket)
    monkeypatch.setattr(webchat_rate_limit, "_enforce_database", lambda _db, _key: None)
    monkeypatch.setattr(webchat_rate_limit.settings, "app_env", "production")
    monkeypatch.setattr(webchat_rate_limit.settings, "webchat_rate_limit_backend", "database")

    webchat_rate_limit.enforce_webchat_rate_limit(
        db,
        _request(),
        tenant_key="default",
        conversation_id=None,
    )
    assert captured["tenant_key"] == "tenant-a"


def test_nonproduction_legacy_fallback_is_explicit(db) -> None:
    scope = resolve_public_webchat_scope(
        db,
        request=_request("http://localhost"),
        requested_tenant_key="local-tenant",
        requested_channel_key="webchat",
        app_env="test",
    )
    assert scope.tenant_key == "local-tenant"
    assert scope.authority == "non_production_legacy"


def test_origin_normalization_rejects_wildcard_credentials_and_insecure_remote_http() -> None:
    for origin in (
        "*",
        "https://user:password@example.com",
        "https://example.com/path",
        "http://example.com",
    ):
        with pytest.raises(HTTPException):
            normalize_public_origin(origin)
    assert normalize_public_origin("HTTPS://Example.COM:443/") == "https://example.com"
    assert normalize_public_origin("http://localhost:3000") == "http://localhost:3000"

def test_public_webchat_stamps_customer_and_ticket_with_verified_relational_tenant(db) -> None:
    tenant = _tenant(db)
    _binding(db)
    resolve_public_webchat_scope(
        db,
        request=_request(),
        requested_tenant_key="default",
        requested_channel_key="default",
        app_env="production",
    )

    result = create_or_resume_conversation(db, _payload(), _request())

    ticket = db.query(Ticket).filter(Ticket.source_chat_id == result["conversation_id"]).one()
    customer = db.get(Customer, ticket.customer_id)
    assert customer is not None
    assert ticket.tenant_id == tenant.id
    assert customer.tenant_id == tenant.id
    assert ticket.tenant_assignment_source == "runtime_principal"
    assert customer.tenant_assignment_source == "runtime_principal"
    assert ticket.tenant_assignment_version == "nexus.tenant.runtime_authority.v1"
    assert customer.tenant_assignment_version == "nexus.tenant.runtime_authority.v1"


@pytest.mark.parametrize("active", [False, None])
def test_public_webchat_rejects_missing_or_inactive_relational_tenant_before_customer_write(db, active) -> None:
    if active is not None:
        _tenant(db, active=active)
    _binding(db)
    resolve_public_webchat_scope(
        db,
        request=_request(),
        requested_tenant_key="default",
        requested_channel_key="default",
        app_env="production",
    )
    before_customers = db.query(Customer).count()
    before_tickets = db.query(Ticket).count()

    with pytest.raises(HTTPException) as exc:
        create_or_resume_conversation(db, _payload(), _request())

    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_tenant_principal_required"
    assert db.query(Customer).count() == before_customers
    assert db.query(Ticket).count() == before_tickets

def test_enforce_mode_never_uses_unverified_payload_tenant_as_authority(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tenant(db)
    monkeypatch.setattr(webchat_service, "tenant_runtime_authority_mode", lambda: "enforce")
    before_customers = db.query(Customer).count()

    with pytest.raises(HTTPException) as exc:
        create_or_resume_conversation(db, _payload(tenant_key="tenant-a"), _request())

    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_verified_scope_required"
    assert db.query(Customer).count() == before_customers

def test_shadow_mode_never_promotes_nonproduction_client_scope_to_relational_tenant(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tenant(db)
    monkeypatch.setattr(webchat_service, "tenant_runtime_authority_mode", lambda: "shadow")
    request = _request("http://localhost")
    scope = resolve_public_webchat_scope(
        db,
        request=request,
        requested_tenant_key="tenant-a",
        requested_channel_key="webchat",
        app_env="test",
    )
    assert scope.authority == "non_production_legacy"

    result = create_or_resume_conversation(
        db,
        _payload(tenant_key="tenant-a", page_url="http://localhost/help"),
        request,
    )

    ticket = db.query(Ticket).filter(Ticket.source_chat_id == result["conversation_id"]).one()
    customer = db.get(Customer, ticket.customer_id)
    assert customer is not None
    assert ticket.tenant_id is None
    assert customer.tenant_id is None
    assert ticket.tenant_assignment_source is None
    assert customer.tenant_assignment_source is None
