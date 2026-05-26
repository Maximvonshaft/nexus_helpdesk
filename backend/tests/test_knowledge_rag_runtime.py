import json
import sys
from pathlib import Path
from types import SimpleNamespace

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
from app.schemas_control_plane import KnowledgeItemCreate, PersonaProfileCreate  # noqa: E402
from app.services import knowledge_service, persona_service  # noqa: E402
from app.services.ai_runtime_context import build_webchat_runtime_context  # noqa: E402
from app.services.knowledge_grounding_service import enforce_grounded_answer  # noqa: E402
from app.services.knowledge_prompt_service import build_knowledge_prompt_block  # noqa: E402
from app.services.knowledge_retrieval_service import analyze_query, retrieve_published_chunks  # noqa: E402
from app.services.provider_runtime.output_contracts import OutputContracts  # noqa: E402
from app.services.webchat_ai_service import _build_prompt  # noqa: E402


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


def test_chinese_natural_query_retrieves_business_fact_above_generic_document(db_session):
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
    assert result.query_analysis.language == "zh"
    assert "瑞士这边改地址要多少钱啊" not in result.query_analysis.terms
    assert len(result.query_analysis.high_value_terms) > 1


def test_english_query_retrieves_approved_business_fact(db_session):
    admin = _user(db_session)
    fact = _business_fact(
        db_session,
        admin,
        item_key="fact.uk.sla",
        title="UK delivery SLA",
        fact_question="What is the UK delivery SLA?",
        fact_answer="The UK delivery SLA is 2 business days after dispatch.",
        fact_aliases_json=["UK delivery time", "UK SLA"],
        language="en",
    )

    result = retrieve_published_chunks(db_session, q="What is the UK delivery time?", channel="website", audience_scope="customer", language="en")

    assert result.hits[0].item_key == fact.item_key
    assert "2 business days" in result.hits[0].direct_answer
    assert "sla" in result.query_analysis.intent_terms or "delivery" in result.query_analysis.service_terms


def test_approved_qa_outranks_raw_chunk_and_unapproved_facts_are_excluded(db_session):
    admin = _user(db_session)
    approved = _business_fact(
        db_session,
        admin,
        item_key="faq.refusal.approved",
        title="Refusal fee FAQ",
        knowledge_kind="faq",
        fact_question="Can customer refuse delivery?",
        fact_answer="Customers may refuse delivery; support must record the refusal reason before return processing.",
        fact_aliases_json=["refuse delivery", "拒收"],
        priority=100,
    )
    _create(
        db_session,
        admin,
        item_key="doc.refusal.generic",
        title="Refusal SOP",
        knowledge_kind="document",
        priority=1,
        draft_body="Refusal delivery procedures include many operational review steps.",
        draft_normalized_text="Refusal delivery procedures include many operational review steps.",
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
    _business_fact(
        db_session,
        admin,
        item_key="faq.refusal.archived",
        title="Archived refusal FAQ",
        status="archived",
        fact_question="Can customer refuse delivery?",
        fact_answer="Archived answer must not be retrieved.",
        fact_aliases_json=["refuse delivery"],
    )

    result = retrieve_published_chunks(db_session, q="Can I refuse delivery?", channel="website", audience_scope="customer")

    keys = [hit.item_key for hit in result.hits]
    assert keys[0] == approved.item_key
    assert "faq.refusal.draft" not in keys
    assert "faq.refusal.archived" not in keys


def test_channel_audience_and_language_filters_are_enforced(db_session):
    admin = _user(db_session)
    website_en = _business_fact(db_session, admin, item_key="fact.website.en", title="Website English", language="en")
    _business_fact(db_session, admin, item_key="fact.email.en", title="Email English", channel="email", language="en")
    _business_fact(db_session, admin, item_key="fact.website.zh", title="Website Chinese", language="zh")
    _business_fact(db_session, admin, item_key="fact.internal.en", title="Internal English", audience_scope="internal", language="en")

    result = retrieve_published_chunks(
        db_session,
        q="Swiss address change fee",
        channel="website",
        audience_scope="customer",
        language="en",
    )

    assert [hit.item_key for hit in result.hits] == [website_en.item_key]


def test_direct_answer_grounding_rewrites_safe_refusal_and_blocks_tracking_boundary(db_session):
    admin = _user(db_session)
    _business_fact(db_session, admin, item_key="fact.ch.address-grounding", title="Swiss address fee")
    result = retrieve_published_chunks(db_session, q="Swiss address change fee", channel="website", audience_scope="customer", language="en")

    decision = enforce_grounded_answer(
        query="Swiss address change fee",
        provider_reply="I cannot confirm that from the available information.",
        hits=result.hits,
    )

    assert decision.applied is True
    assert decision.reply == "The Switzerland address-change service fee is 8 CHF."

    blocked = enforce_grounded_answer(
        query="Where is package PK120053679836?",
        provider_reply="I cannot confirm that from the available information.",
        hits=result.hits,
        tracking_fact_evidence_present=False,
    )
    assert blocked.applied is False


def test_direct_answer_grounding_rewrites_safe_numeric_contradiction_only():
    hits = [
        {
            "item_key": "fact.shipping.sla",
            "title": "运输时效",
            "score": 42.0,
            "chunk_index": 0,
            "retrieval_method": "structured_fact_recall+direct_answer_fact",
            "direct_answer": "海运15天，空运10天。",
            "answer_mode": "direct_answer",
            "metadata": {"knowledge_kind": "business_fact", "fact_status": "approved", "answer_mode": "direct_answer"},
            "source_metadata": {"item_key": "fact.shipping.sla"},
        }
    ]

    decision = enforce_grounded_answer(
        query="海运和空运多久？",
        provider_reply="通常需要30-45天。",
        hits=hits,
    )

    assert decision.applied is True
    assert decision.reply == "海运15天，空运10天。"
    assert decision.reason == "direct_answer_conflict_rewrite"

    legal_blocked = enforce_grounded_answer(
        query="如果海运晚了要赔偿吗，海运多久？",
        provider_reply="通常需要30-45天。",
        hits=hits,
    )
    assert legal_blocked.applied is False

    tracking_blocked = enforce_grounded_answer(
        query="PK120053679836 现在在哪里，海运多久？",
        provider_reply="通常需要30-45天。",
        hits=hits,
        tracking_fact_evidence_present=True,
    )
    assert tracking_blocked.applied is False


def test_runtime_context_and_prompt_are_bounded_and_sanitized(db_session):
    admin = _user(db_session)
    _create(
        db_session,
        admin,
        item_key="doc.return.window",
        title="Return window policy",
        draft_body="Return window is 7 days. Bearer sk-not-a-real-secret http://127.0.0.1/private",
        draft_normalized_text="Return window is 7 days. Bearer sk-not-a-real-secret http://127.0.0.1/private",
    )

    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        body="What is the return window?",
        language="en",
    )
    encoded = json.dumps(context, ensure_ascii=False)
    assert "Bearer" not in encoded
    assert "127.0.0.1" not in encoded
    assert context["knowledge_context"]["query_analysis"]["language"] == "en"

    prompt = _build_prompt(
        ticket=SimpleNamespace(ticket_no="T-1"),
        conversation=SimpleNamespace(),
        visitor_message=SimpleNamespace(body="What is the return window?"),
        history_rows=[SimpleNamespace(direction="visitor", body="What is the return window?")],
        runtime_context=context,
    )
    assert "item_key=doc.return.window" in prompt
    assert "do not say cannot confirm" in prompt
    assert len(prompt) < 7000


def test_persona_identity_contract_still_overrides_provider_output(db_session):
    admin = _user(db_session)
    profile = persona_service.create_profile(
        db_session,
        PersonaProfileCreate(
            profile_key="identity.website.zh",
            name="Identity Website Chinese",
            channel="website",
            language="zh",
            draft_summary="Identity contract only.",
            draft_content_json={
                "brand_name": "猴王山",
                "assistant_name": "悟空客服",
                "identity_statement": "我是猴王山的悟空客服，可以协助处理客户服务问题。",
                "disallowed_identity_claims": ["NexusDesk"],
            },
        ),
        admin,
    )
    persona_service.publish_profile(db_session, profile, admin, notes="publish")
    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="zh",
        body="你是谁",
    )
    parsed = OutputContracts.validate_and_parse(
        "speedaf_webchat_fast_reply_v1",
        json.dumps({"customer_reply": "我是 NexusDesk。", "language": "zh", "intent": "other", "handoff_required": False, "ticket_should_create": False}),
        evidence_present=False,
        persona_context=context["persona_context"],
        request_body="你是谁",
    )

    assert parsed["customer_reply"] == "我是猴王山的悟空客服，可以协助处理客户服务问题。"
    assert "NexusDesk" not in parsed["customer_reply"]


def test_knowledge_prompt_block_force_includes_direct_answer_first(db_session):
    admin = _user(db_session)
    _business_fact(db_session, admin, item_key="fact.direct.prompt", title="Direct prompt fact")
    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Swiss address change fee",
    )

    block = build_knowledge_prompt_block(context["knowledge_context"])

    assert "[KB 1] item_key=fact.direct.prompt" in block
    assert "direct_answer=The Switzerland address-change service fee is 8 CHF." in block
    assert "not live parcel tracking evidence" in block
