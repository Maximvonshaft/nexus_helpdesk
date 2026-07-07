import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_knowledge_seed_contract.db")
os.environ.setdefault("KNOWLEDGE_RUNTIME_VERSION", "v2")
os.environ.setdefault("KNOWLEDGE_EMBEDDINGS_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import Team, User  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate, KnowledgePublishRequest  # noqa: E402
from app.services import knowledge_service  # noqa: E402
from app.services.knowledge_runtime_v2 import retrieve_knowledge  # noqa: E402
import app.models_control_plane  # noqa: F401,E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "knowledge_seed_contract.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _actor(db_session) -> User:
    team = Team(name=f"Ops-{_uid()}", team_type="support")
    user = User(
        username=f"agent-{_uid()}",
        display_name="Agent",
        email=f"agent-{_uid()}@example.com",
        password_hash=hash_password("pass123"),
        role=UserRole.admin,
        team_id=team.id,
        is_active=True,
    )
    db_session.add_all([team, user])
    db_session.flush()
    return user


def _publish(db_session, actor, **overrides):
    data = {
        "item_key": f"kb.{_uid()}",
        "title": "Knowledge",
        "summary": "Knowledge",
        "status": "draft",
        "source_type": "text",
        "knowledge_kind": "faq",
        "tenant_id": "default",
        "brand_id": "default",
        "country_scope": "GLOBAL",
        "channel_scope": "all",
        "locale": "en",
        "visibility": "customer",
        "shareability": "customer_visible",
        "authority_level": "faq",
        "risk_level": "low",
        "audience_scope": "customer",
        "language": "en",
        "priority": 100,
        "fact_question": "How do returns work?",
        "fact_answer": "Global returns require support review.",
        "fact_status": "approved",
        "answer_mode": "direct_answer",
        "draft_body": "How do returns work?\nGlobal returns require support review.",
    }
    data.update(overrides)
    item = knowledge_service.create_item(db_session, KnowledgeItemCreate(**data), actor)
    knowledge_service.publish_item(db_session, item, actor, notes=KnowledgePublishRequest().notes)
    db_session.flush()
    return item


def _retrieve(db_session, query, *, country, channel="webchat"):
    return retrieve_knowledge(
        db_session,
        query=query,
        tenant_key="default",
        brand_id="default",
        country_scope=country,
        channel_scope=channel,
        channel=channel,
        language="en",
        limit=5,
    )


def test_us_specific_knowledge_beats_global(db_session):
    actor = _actor(db_session)
    _publish(db_session, actor, item_key=f"kb.global.{_uid()}", country_scope="GLOBAL", fact_answer="Global returns require support review.", draft_body="How do returns work?\nGlobal returns require support review.")
    _publish(db_session, actor, item_key=f"kb.us.{_uid()}", country_scope="US", fact_answer="US returns follow the US policy.", draft_body="How do returns work?\nUS returns follow the US policy.")
    result = _retrieve(db_session, "How do returns work?", country="US")
    assert result.hits
    assert result.hits[0].metadata["country_scope"] == "US"


def test_mx_does_not_receive_us_policy(db_session):
    actor = _actor(db_session)
    _publish(db_session, actor, item_key=f"kb.us.{_uid()}", country_scope="US", fact_answer="US returns follow the US policy.", draft_body="How do returns work?\nUS returns follow the US policy.")
    _publish(db_session, actor, item_key=f"kb.mx.{_uid()}", country_scope="MX", fact_answer="MX returns follow the MX policy.", draft_body="How do returns work?\nMX returns follow the MX policy.")
    result = _retrieve(db_session, "How do returns work?", country="MX")
    assert result.hits
    assert result.hits[0].metadata["country_scope"] == "MX"
    assert all(hit.metadata["country_scope"] != "US" for hit in result.hits)


def test_missing_country_falls_back_to_global(db_session):
    actor = _actor(db_session)
    _publish(db_session, actor, item_key=f"kb.global.{_uid()}", country_scope="GLOBAL", fact_answer="Global help is available.", draft_body="Need help?\nGlobal help is available.")
    result = _retrieve(db_session, "Need help?", country="FR")
    assert result.hits
    assert {hit.metadata["country_scope"] for hit in result.hits} == {"GLOBAL"}


def test_whatsapp_does_not_hit_webchat_only_knowledge(db_session):
    actor = _actor(db_session)
    _publish(db_session, actor, item_key=f"kb.webchat.{_uid()}", channel_scope="webchat", fact_answer="WebChat long explanation.", draft_body="Channel help\nWebChat long explanation.")
    result = _retrieve(db_session, "Channel help", country="GLOBAL", channel="whatsapp")
    assert result.hits == []


def test_internal_only_knowledge_not_used_as_customer_visible_source(db_session):
    actor = _actor(db_session)
    _publish(db_session, actor, item_key=f"kb.internal.{_uid()}", visibility="internal", shareability="internal_only", fact_question="Internal route", fact_answer="Internal-only route.", draft_body="Internal route\nInternal-only route.")
    result = _retrieve(db_session, "Internal route", country="GLOBAL")
    assert result.hits == []
