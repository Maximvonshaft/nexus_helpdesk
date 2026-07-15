import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models  # noqa: F401,E402
from app import models_control_plane  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate  # noqa: E402
from app.services import knowledge_service  # noqa: E402
from app.services.knowledge_retrieval_service import retrieve_published_chunks  # noqa: E402
from app.services.knowledge_runtime_v2 import retrieve_knowledge  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _user(session) -> User:
    row = User(
        username="admin",
        display_name="Admin",
        email="admin@example.test",
        password_hash="not-a-real-password-hash",
        role=UserRole.admin,
        is_active=True,
    )
    session.add(row)
    session.flush()
    return row


def _create(session, admin, **overrides):
    data = {
        "item_key": "kb.item",
        "title": "Knowledge Item",
        "summary": "Knowledge summary",
        "status": "draft",
        "source_type": "text",
        "knowledge_kind": "document",
        "tenant_id": "default",
        "brand_id": "default",
        "country_scope": "GLOBAL",
        "channel_scope": "all",
        "visibility": "customer",
        "shareability": "customer_visible",
        "authority_level": "faq",
        "risk_level": "low",
        "channel": None,
        "audience_scope": "customer",
        "language": None,
        "priority": 100,
        "fact_status": "draft",
        "answer_mode": "guided_answer",
        "draft_body": "Customers can change address before dispatch.",
        "draft_normalized_text": "Customers can change address before dispatch.",
    }
    data.update(overrides)
    item = knowledge_service.create_item(session, KnowledgeItemCreate(**data), admin)
    knowledge_service.publish_item(session, item, admin, notes="test publish")
    return item


def _business_fact(session, admin, **overrides):
    data = {
        "knowledge_kind": "business_fact",
        "fact_status": "approved",
        "answer_mode": "direct_answer",
        "draft_body": None,
        "draft_normalized_text": None,
        "fact_question": "Do you provide domestic-to-domestic delivery in Switzerland?",
        "fact_answer": "Switzerland domestic-to-domestic service is currently unavailable.",
        "fact_aliases_json": [
            "domestic to domestic delivery in Switzerland",
            "Swiss local delivery",
            "service availability Switzerland",
            "瑞士本对本",
        ],
        "language": "mixed",
        "priority": 6,
    }
    data.update(overrides)
    return _create(session, admin, **data)


def _ch_service_availability(session, admin, **overrides):
    return _business_fact(
        session,
        admin,
        item_key="nexus.support.customer.kb.ch.service.availability",
        title="Switzerland domestic-to-domestic service availability",
        country_scope="GLOBAL",
        channel_scope="all",
        **overrides,
    )


def _shipment_fact(session, admin):
    return _business_fact(
        session,
        admin,
        item_key="fact.shipment.policy",
        title="Shipment policy",
        fact_question="What is shipment policy?",
        fact_answer="Shipment policy requires a valid order reference.",
        fact_aliases_json=["shipment policy"],
        priority=20,
    )


def test_stopwords_do_not_retrieve_direct_answer(db_session):
    admin = _user(db_session)
    _ch_service_availability(db_session, admin)

    result = retrieve_published_chunks(db_session, q="hi", channel="whatsapp", audience_scope="customer", language="en")

    assert result.hits == []
    assert result.grounding_would_apply is False
    assert "hi" in result.runtime_trace["query"]["dropped_stopwords"]


def test_german_general_support_question_does_not_retrieve_business_fact(db_session):
    admin = _user(db_session)
    _business_fact(
        db_session,
        admin,
        item_key="fact.delivery.acceleration",
        title="Delivery acceleration",
        fact_question="Kann die Zustellung beschleunigt werden?",
        fact_answer="Eine Beschleunigung kann als Zustellanfrage erfasst werden.",
        fact_aliases_json=["Zustellung beschleunigen", "Beschleunigung der Lieferung"],
        language="de",
    )

    result = retrieve_published_chunks(
        db_session,
        q="Hallo, womit kannst du mir helfen?",
        channel="webchat",
        audience_scope="customer",
        language="de",
    )

    assert result.hits == []
    assert result.grounding_would_apply is False
    assert {"hallo", "womit", "kannst", "du", "mir", "helfen"}.issubset(
        set(result.runtime_trace["query"]["dropped_stopwords"])
    )


def test_hi_does_not_match_shipment_substring(db_session):
    admin = _user(db_session)
    _shipment_fact(db_session, admin)

    result = retrieve_published_chunks(db_session, q="hi", channel="webchat", audience_scope="customer", language="en")

    assert result.hits == []


def test_ch_does_not_match_check_or_architecture(db_session):
    admin = _user(db_session)
    _business_fact(
        db_session,
        admin,
        item_key="fact.check.architecture",
        title="Check architecture policy",
        fact_question="How does check architecture work?",
        fact_answer="Check architecture is an internal implementation detail.",
        fact_aliases_json=["check architecture"],
    )

    result = retrieve_published_chunks(db_session, q="CH", channel="webchat", audience_scope="customer", language="en")

    assert result.hits == []
    assert "ch" in result.runtime_trace["query"]["country_terms"]
    assert "ch" in result.runtime_trace["query"]["dropped_stopwords"]


def test_to_does_not_unlock_domestic_to_domestic_direct_answer(db_session):
    admin = _user(db_session)
    _ch_service_availability(db_session, admin)

    result = retrieve_published_chunks(db_session, q="to tomorrow", channel="whatsapp", audience_scope="customer", language="en")

    assert result.hits == []
    assert result.grounding_would_apply is False
    assert "to" in result.runtime_trace["query"]["dropped_stopwords"]


@pytest.mark.parametrize("channel,query", [("whatsapp", "Vasil finalized AI not so smart"), ("webchat", "logo sign ready tomorrow")])
def test_unrelated_channel_message_does_not_lock_ch_service_availability(db_session, channel, query):
    admin = _user(db_session)
    _ch_service_availability(db_session, admin)

    result = retrieve_published_chunks(db_session, q=query, channel=channel, audience_scope="customer", language="en")

    assert result.grounding_would_apply is False
    assert result.grounding_source is None
    assert all(hit.direct_answer is None for hit in result.hits)


def test_ch_service_availability_query_can_lock_direct_answer(db_session):
    admin = _user(db_session)
    fact = _ch_service_availability(db_session, admin)

    result = retrieve_published_chunks(
        db_session,
        q="Is Switzerland domestic-to-domestic service available?",
        channel="webchat",
        audience_scope="customer",
        language="en",
    )

    assert result.hits[0].item_key == fact.item_key
    assert result.hits[0].direct_answer == "Switzerland domestic-to-domestic service is currently unavailable."
    assert result.grounding_would_apply is True


def test_filtered_terms_and_dropped_stopwords_in_trace(db_session):
    admin = _user(db_session)
    _ch_service_availability(db_session, admin)

    runtime = retrieve_knowledge(db_session, query="please check this", channel_scope="all", channel="webchat", language="en")

    query_trace = runtime.trace["query"]
    assert "please" in query_trace["dropped_stopwords"]
    assert "this" in query_trace["dropped_stopwords"]
    assert runtime.locked_facts == []


def test_country_code_not_used_as_plain_text_term(db_session):
    admin = _user(db_session)
    _ch_service_availability(db_session, admin)

    runtime = retrieve_knowledge(db_session, query="CH", channel_scope="all", channel="webchat", language="en")

    assert runtime.hits == []
    assert "ch" not in runtime.trace["query"]["filtered_terms"]
    assert "ch" in runtime.trace["query"]["country_terms"]
