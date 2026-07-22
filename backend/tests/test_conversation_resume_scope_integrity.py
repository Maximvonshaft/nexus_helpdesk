from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_conversation_resume_scope_integrity.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import (  # noqa: E402,F401
    models,
    models_agent_routing,
    models_control_plane,
    models_operations_dispatch,
    models_osr,
    operator_models,
    tool_models,
    voice_models,
    webchat_models,
)
from app.api.webchat_public import WebchatInitRequest  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Customer  # noqa: E402
from app.models_agent_routing import ConversationControl  # noqa: E402
from app.services import conversation_first_service  # noqa: E402
from app.services.webchat_session_identity import hash_token  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'resume_scope_integrity.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/webchat/init",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
            "query_string": b"",
        }
    )


def test_resume_rejects_country_scope_change_without_rewriting_control(
    db_session,
    monkeypatch,
):
    token = "valid-resume-token"
    customer = Customer(name="Scope Customer", external_ref="scope-customer")
    db_session.add(customer)
    db_session.flush()
    conversation = WebchatConversation(
        public_id="conversation-scope-integrity",
        visitor_token_hash=hash_token(token),
        tenant_key="default",
        channel_key="webchat",
        ticket_id=None,
        visitor_name=customer.name,
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    control = ConversationControl(
        conversation_id=conversation.id,
        customer_id=customer.id,
        tenant_key="default",
        country_code="ME",
        channel_key="webchat",
    )
    db_session.add(control)
    db_session.flush()

    monkeypatch.setattr(
        conversation_first_service,
        "current_verified_webchat_scope",
        lambda _db: SimpleNamespace(
            tenant_key="default",
            country_code="AL",
            channel_key="webchat",
            authority="server_origin_binding",
        ),
    )
    monkeypatch.setattr(
        conversation_first_service,
        "_relational_tenant",
        lambda _db: None,
    )

    with pytest.raises(HTTPException) as exc:
        conversation_first_service.create_or_resume_conversation(
            db_session,
            WebchatInitRequest(
                tenant_key="default",
                channel_key="webchat",
                conversation_id=conversation.public_id,
                visitor_token=token,
            ),
            _request(),
        )

    assert exc.value.status_code == 409
    db_session.refresh(control)
    assert control.country_code == "ME"
