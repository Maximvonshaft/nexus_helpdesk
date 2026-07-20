from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_ticketless_support_scope_tests.db",
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
from app.api.support_conversations import list_support_conversations  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import Customer, User  # noqa: E402
from app.models_agent_routing import ConversationControl  # noqa: E402
from app.operator_models import OperatorQueueScopeGrant  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "ticketless_support_scope.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
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


def test_admin_requires_explicit_scope_for_ticketless_support_views(db_session):
    admin = User(
        username="unscoped-support-admin",
        display_name="Unscoped Support Admin",
        password_hash="not-used",
        role=UserRole.admin,
        is_active=True,
    )
    customer = Customer(
        name="Scoped Customer",
        external_ref="scoped-customer",
    )
    db_session.add_all([admin, customer])
    db_session.flush()
    conversation = WebchatConversation(
        public_id="ticketless-scope-isolation",
        visitor_token_hash="ticketless-scope-isolation-hash",
        tenant_key="default",
        channel_key="webchat",
        ticket_id=None,
        visitor_name=customer.name,
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        ConversationControl(
            conversation_id=conversation.id,
            customer_id=customer.id,
            tenant_key="default",
            country_code="ME",
            channel_key="webchat",
        )
    )
    db_session.flush()

    hidden = list_support_conversations(
        q=None,
        channel="webchat",
        view="open",
        limit=80,
        db=db_session,
        current_user=admin,
    )
    assert hidden["items"] == []

    db_session.add(
        OperatorQueueScopeGrant(
            user_id=admin.id,
            tenant_key="default",
            country_code="ME",
            channel_key="webchat",
            enabled=True,
        )
    )
    db_session.flush()
    visible = list_support_conversations(
        q=None,
        channel="webchat",
        view="open",
        limit=80,
        db=db_session,
        current_user=admin,
    )
    assert [item["conversation_id"] for item in visible["items"]] == [
        conversation.public_id
    ]
