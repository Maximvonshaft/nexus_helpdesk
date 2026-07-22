from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.db import Base
from app.model_registry import register_all_models
from app.models import Tenant, Ticket
from app.models_agent_routing import ConversationControl
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.services.conversation_first_service import create_or_resume_conversation
from app.services.webchat_tenant_binding import resolve_public_webchat_scope
from app.webchat_models import WebchatConversation


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/webchat/init",
            "raw_path": b"/api/webchat/init",
            "query_string": b"",
            "headers": [(b"origin", b"https://country.example")],
            "client": ("203.0.113.20", 50000),
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


def _seed(db, *, country_code: str | None = "CH") -> WebchatPublicOriginBinding:
    db.add(
        Tenant(
            tenant_key="country-tenant",
            display_name="Country Tenant",
            is_active=True,
        )
    )
    row = WebchatPublicOriginBinding(
        normalized_origin="https://country.example",
        tenant_key="country-tenant",
        country_code=country_code,
        channel_key="website",
        display_name="Country widget",
        is_active=True,
    )
    db.add(row)
    db.commit()
    return row


def _payload(**overrides):
    values = {
        "tenant_key": "default",
        "channel_key": "default",
        "conversation_id": None,
        "visitor_token": None,
        "visitor_name": "Country Visitor",
        "visitor_email": None,
        "visitor_phone": None,
        "visitor_ref": None,
        "origin": None,
        "page_url": "https://country.example/help",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_server_origin_country_is_stamped_on_conversation_control(db) -> None:
    _seed(db)
    scope = resolve_public_webchat_scope(
        db,
        request=_request(),
        requested_tenant_key="default",
        requested_channel_key="default",
        app_env="production",
    )

    result = create_or_resume_conversation(db, _payload(), _request())
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == result["conversation_id"])
        .one()
    )
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .one()
    )

    assert scope.country_code == "CH"
    assert control.country_code == "CH"
    assert conversation.ticket_id is None
    assert db.query(Ticket).count() == 0


def test_missing_configured_country_fails_closed(db) -> None:
    _seed(db, country_code=None)
    with pytest.raises(HTTPException) as exc:
        resolve_public_webchat_scope(
            db,
            request=_request(),
            requested_tenant_key="default",
            requested_channel_key="default",
            app_env="production",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_public_country_scope_required"


def test_invalid_configured_country_fails_closed(db) -> None:
    _seed(db, country_code="CHE")
    with pytest.raises(HTTPException) as exc:
        resolve_public_webchat_scope(
            db,
            request=_request(),
            requested_tenant_key="default",
            requested_channel_key="default",
            app_env="production",
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_webchat_country_scope"


def test_country_change_cannot_resume_an_existing_conversation(db) -> None:
    binding = _seed(db)
    scope = resolve_public_webchat_scope(
        db,
        request=_request(),
        requested_tenant_key="default",
        requested_channel_key="default",
        app_env="production",
    )
    result = create_or_resume_conversation(db, _payload(), _request())
    assert scope.country_code == "CH"

    binding.country_code = "ME"
    db.commit()
    with pytest.raises(HTTPException) as exc:
        resolve_public_webchat_scope(
            db,
            request=_request(),
            requested_tenant_key="default",
            requested_channel_key="default",
            conversation_id=result["conversation_id"],
            app_env="production",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "webchat_public_scope_mismatch"
