from __future__ import annotations

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
from app.models_control_plane import KnowledgeItemVersion  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate  # noqa: E402
from app.services import knowledge_service  # noqa: E402
from app.services.nexus_osr.tool_execution_policy_seed import seed_default_tool_execution_policies  # noqa: E402
from app.services.agent_runtime.tool_adapter import (  # noqa: E402
    AgentExecutionContext,
    execute_agent_tool_calls,
)
from app.services.ai_runtime_context import build_agent_context  # noqa: E402
from app.services.knowledge_retrieval_service import retrieve_published_chunks  # noqa: E402
from app.services.webchat_ai_decision_runtime.schemas import AIDecisionToolCall  # noqa: E402


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
        "channel": "website",
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
        "fact_question": "How much is address change in Switzerland?",
        "fact_answer": "The Switzerland address-change service fee is 8 CHF.",
        "fact_aliases_json": ["Swiss address change fee", "瑞士改地址费用"],
    }
    data.update(overrides)
    return _create(session, admin, **data)


def _release_snapshot(session, item) -> dict:
    version = (
        session.query(KnowledgeItemVersion)
        .filter(
            KnowledgeItemVersion.item_id == item.id,
            KnowledgeItemVersion.version == item.published_version,
        )
        .one()
    )
    return {
        "source": "deployment",
        "release": {"id": 1, "version": 1},
        "resolved": {
            "knowledge": [
                {
                    "id": item.id,
                    "item_key": item.item_key,
                    "version": version.version,
                    "snapshot": version.snapshot_json,
                }
            ]
        },
    }


def test_chinese_query_retrieves_approved_business_fact_above_generic_document(db_session):
    admin = _user(db_session)
    fact = _business_fact(
        db_session,
        admin,
        item_key="fact.ch.address-fee",
        title="瑞士改地址费用",
        language="zh",
        fact_question="瑞士改地址多少钱？",
        fact_answer="瑞士地址变更服务费为 8 CHF。",
        fact_aliases_json=["瑞士改地址费用", "CH地址变更价格"],
        priority=80,
    )
    _create(
        db_session,
        admin,
        item_key="sop.generic-address",
        title="Generic Address Change SOP",
        knowledge_kind="sop",
        language="zh",
        priority=1,
        draft_body="地址变更需要客服核实。不要承诺所有国家都能改地址。",
        draft_normalized_text="地址变更需要客服核实。不要承诺所有国家都能改地址。",
    )

    result = retrieve_published_chunks(
        db_session,
        q="瑞士这边改地址要多少钱啊",
        channel="website",
        audience_scope="customer",
        language="zh",
        limit=5,
    )

    assert result.hits[0].item_key == fact.item_key
    assert result.hits[0].direct_answer == "瑞士地址变更服务费为 8 CHF。"
    assert result.hits[0].score > result.hits[1].score


def test_english_and_mixed_language_facts_remain_retrievable(db_session):
    admin = _user(db_session)
    uk = _business_fact(
        db_session,
        admin,
        item_key="fact.uk.sla",
        title="UK delivery SLA",
        fact_question="What is the UK delivery SLA?",
        fact_answer="The UK delivery SLA is 2 business days after dispatch.",
        fact_aliases_json=["UK delivery time", "UK SLA"],
        language="en",
    )
    swiss = _business_fact(
        db_session,
        admin,
        item_key="fact.ch.service-availability",
        title="Switzerland domestic-to-domestic service availability",
        language="mixed",
        fact_question="Do you provide domestic-to-domestic delivery in Switzerland?",
        fact_answer="Switzerland domestic-to-domestic service is currently unavailable. 瑞士目前暂未开通本对本业务。",
        fact_aliases_json=["Swiss local delivery", "瑞士本对本"],
    )

    uk_result = retrieve_published_chunks(
        db_session,
        q="What is the UK delivery time?",
        channel="website",
        audience_scope="customer",
        language="en",
    )
    swiss_result = retrieve_published_chunks(
        db_session,
        q="Do you provide domestic to domestic delivery in Switzerland?",
        channel="website",
        audience_scope="customer",
        language="en",
    )

    assert uk_result.hits[0].item_key == uk.item_key
    assert "2 business days" in uk_result.hits[0].direct_answer
    assert swiss_result.hits[0].item_key == swiss.item_key
    assert "currently unavailable" in swiss_result.hits[0].direct_answer


def test_unapproved_and_archived_facts_are_not_returned(db_session):
    admin = _user(db_session)
    approved = _business_fact(
        db_session,
        admin,
        item_key="faq.refusal.approved",
        title="Refusal fee FAQ",
        knowledge_kind="faq",
        fact_question="Can customer refuse delivery?",
        fact_answer="Customers may refuse delivery; support must record the refusal reason.",
        fact_aliases_json=["refuse delivery", "拒收"],
    )
    _business_fact(
        db_session,
        admin,
        item_key="faq.refusal.draft",
        title="Draft refusal FAQ",
        fact_status="draft",
        fact_question="Can customer refuse delivery?",
        fact_answer="Draft answer must not be retrieved.",
        fact_aliases_json=["refuse delivery"],
    )
    archived = _business_fact(
        db_session,
        admin,
        item_key="faq.refusal.archived",
        title="Archived refusal FAQ",
        fact_question="Can customer refuse delivery?",
        fact_answer="Archived answer must not be retrieved.",
        fact_aliases_json=["refuse delivery"],
    )
    archived.status = "archived"
    db_session.flush()

    result = retrieve_published_chunks(
        db_session,
        q="Can customer refuse delivery?",
        channel="website",
        audience_scope="customer",
        language="en",
        limit=10,
    )

    keys = {hit.item_key for hit in result.hits}
    assert approved.item_key in keys
    assert "faq.refusal.draft" not in keys
    assert "faq.refusal.archived" not in keys


def test_generic_runtime_context_does_not_prefetch_knowledge(db_session):
    admin = _user(db_session)
    _business_fact(
        db_session,
        admin,
        item_key="fact.ch.shipping-sla",
        title="瑞士海运时效",
        language="zh",
        fact_question="瑞士海运时效是多少？",
        fact_answer="瑞士海运时效为 15 天。",
        fact_aliases_json=["瑞士海运多久", "瑞士海运时效"],
    )

    context = build_agent_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        body="瑞士海运时效是多少？",
        language="zh",
    )

    assert context["context_version"] == "nexus.agent_context.v4"
    assert context["agent_release_error"] == "agent_deployment_unavailable"
    assert context["customer_confirmation"] is None
    assert "session_checkpoint" not in context
    assert "knowledge_context" not in context
    assert "locked_facts" not in str(context)


def test_knowledge_search_tool_returns_safe_release_bound_observation(db_session):
    admin = _user(db_session)
    fact = _business_fact(
        db_session,
        admin,
        item_key="fact.ch.shipping-sla",
        title="瑞士海运时效",
        language="zh",
        fact_question="瑞士海运时效是多少？",
        fact_answer="瑞士海运时效为 15 天。",
        fact_aliases_json=["瑞士海运多久", "瑞士海运时效"],
    )
    seed_default_tool_execution_policies(db_session)
    context = AgentExecutionContext(
        tenant_key="default",
        channel_key="website",
        session_id="session",
        request_id="request",
        customer_message="瑞士海运时效是多少？",
        language="zh",
        allowed_tools=frozenset({"knowledge.search"}),
        granted_permissions=frozenset({"knowledge:read"}),
        release_snapshot=_release_snapshot(db_session, fact),
    )

    observations = execute_agent_tool_calls(
        db_session,
        calls=[
            AIDecisionToolCall(
                tool_name="knowledge.search",
                arguments={"query": "瑞士海运时效是多少？", "limit": 3},
            )
        ],
        context=context,
    )

    assert len(observations) == 1
    assert observations[0].ok is True
    assert observations[0].status == "executed"
    assert observations[0].result["hits"][0]["source_id"] == fact.item_key
    assert observations[0].result["hits"][0]["answer"] == "瑞士海运时效为 15 天。"
