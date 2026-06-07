import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402
from app import models_control_plane  # noqa: F401,E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.api.webchat_fast import _answer_from_knowledge_hit  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate, KnowledgePublishRequest, PersonaProfileCreate, PersonaPublishRequest  # noqa: E402
from app.services import knowledge_service, persona_service  # noqa: E402
from app.services.ai_runtime_context import build_webchat_runtime_context  # noqa: E402
from app.services.knowledge_retrieval_service import search_published_chunks  # noqa: E402
from app.services.knowledge_runtime_v2 import runtime as knowledge_runtime  # noqa: E402
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


def _knowledge_payload(**overrides) -> KnowledgeItemCreate:
    data = {
        "item_key": "address.policy",
        "title": "Address Change Policy",
        "summary": "Customer address change rules",
        "status": "draft",
        "source_type": "text",
        "channel": "website",
        "audience_scope": "customer",
        "priority": 10,
        "draft_body": "Customers can change delivery address before dispatch only.",
        "draft_normalized_text": "change delivery address before dispatch",
    }
    data.update(overrides)
    return KnowledgeItemCreate(**data)


def test_guided_tracking_fallback_extracts_answer_from_single_line_structured_chunk():
    answer = _answer_from_knowledge_hit({
        "text": "Question: 客户输入瑞士 Speedaf 运单号查不到怎么办？ Alias: CH运单号格式 Answer: 请客户核对 CH 开头后接 12 位数字的完整运单号。",
    })

    assert answer == "请客户核对 CH 开头后接 12 位数字的完整运单号。"


def test_upload_text_document_sets_parse_fields_and_draft(monkeypatch, db_session):
    admin = _user(db_session)
    item = knowledge_service.create_item(db_session, _knowledge_payload(item_key="upload.policy", draft_body=None), admin)

    monkeypatch.setattr(
        knowledge_service.file_service,
        "save_upload",
        lambda file: SimpleNamespace(
            stored_name=file.filename,
            storage_key="stored-faq.txt",
            file_size=42,
            mime_type="text/plain",
        ),
    )

    uploaded = UploadFile(filename="faq.txt", file=BytesIO(b"Customers may change address before dispatch."), headers=Headers({"content-type": "text/plain"}))
    updated = knowledge_service.upload_document(db_session, item, uploaded, admin)

    assert updated.source_type == "file"
    assert updated.file_storage_key == "stored-faq.txt"
    assert updated.parsing_status == "parsed"
    assert updated.parsing_error is None
    assert updated.draft_body == "Customers may change address before dispatch."
    assert updated.draft_normalized_text == "Customers may change address before dispatch."


def test_upload_document_extracts_business_fact_draft(monkeypatch, db_session):
    admin = _user(db_session)
    item = knowledge_service.create_item(db_session, _knowledge_payload(item_key="upload.ch-waybill", title="ch-waybill.txt", draft_body=None), admin)

    monkeypatch.setattr(
        knowledge_service.file_service,
        "save_upload",
        lambda file: SimpleNamespace(
            stored_name=file.filename,
            storage_key="stored-ch-waybill.txt",
            file_size=128,
            mime_type="text/plain",
        ),
    )

    body = "\n".join([
        "标题：瑞士 Speedaf 运单号格式与输错提醒",
        "问题：客户输入瑞士 Speedaf 运单号查不到怎么办？",
        "答案：请客户核对 CH 开头后接 12 位数字的完整运单号，不得在无可信查单结果时判断物流状态。",
        "关键词：CH运单号格式，订单号输错，waybill not found",
    ])
    uploaded = UploadFile(filename="ch-waybill.txt", file=BytesIO(body.encode("utf-8")), headers=Headers({"content-type": "text/plain"}))
    updated = knowledge_service.upload_document(db_session, item, uploaded, admin)

    assert updated.knowledge_kind == "business_fact"
    assert updated.fact_status == "draft"
    assert updated.answer_mode == "guided_answer"
    assert updated.fact_question == "客户输入瑞士 Speedaf 运单号查不到怎么办？"
    assert "CH 开头后接 12 位数字" in updated.fact_answer
    assert "waybill not found" in updated.fact_aliases_json
    assert updated.citation_metadata_json["document_extraction"]["requires_human_review"] is True


def test_publish_indexes_chunks_and_retrieval_respects_metadata_filters(db_session):
    admin = _user(db_session)
    active = knowledge_service.create_item(
        db_session,
        _knowledge_payload(item_key="website.address", channel="website", priority=5),
        admin,
    )
    wrong_channel = knowledge_service.create_item(
        db_session,
        _knowledge_payload(item_key="email.address", channel="email", priority=1),
        admin,
    )
    archived = knowledge_service.create_item(
        db_session,
        _knowledge_payload(item_key="archived.address", channel="website", status="archived"),
        admin,
    )
    for item in (active, wrong_channel, archived):
        knowledge_service.publish_item(db_session, item, admin, notes="publish")

    hits, total = search_published_chunks(
        db_session,
        q="change delivery address",
        channel="website",
        audience_scope="customer",
        limit=5,
    )

    assert active.chunk_count > 0
    assert total == 1
    assert [hit.item_key for hit in hits] == ["website.address"]


def test_runtime_context_includes_published_persona_and_safe_knowledge(db_session):
    admin = _user(db_session)
    profile = persona_service.create_profile(
        db_session,
        PersonaProfileCreate(
            profile_key="default.website.en",
            name="Default Website English",
            channel="website",
            language="en",
            draft_summary="Be concise and never invent tracking status.",
            draft_content_json={"tone": "concise"},
        ),
        admin,
    )
    persona_service.publish_profile(db_session, profile, admin, notes="publish")
    item = knowledge_service.create_item(db_session, _knowledge_payload(item_key="runtime.address", channel="website"), admin)
    knowledge_service.publish_item(db_session, item, admin, notes="publish")

    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Can I change my delivery address?",
    )

    assert context["persona_context"]["profile_key"] == "default.website.en"
    assert context["knowledge_context"]["retrieval"] == "hybrid_rag_v2"
    assert context["rag_trace"]["retrieval"] == "hybrid_rag_v2"
    assert context["knowledge_context"]["hits"][0]["item_key"] == "runtime.address"
    assert context["safety_policy"]["knowledge_scope"] == "policy_sop_faq_only"
    assert "tracking_fact_evidence_present=true" in context["safety_policy"]["tracking_truth_boundary"]


def test_runtime_context_expands_tracking_no_evidence_query_to_waybill_rules(db_session):
    admin = _user(db_session)
    item = knowledge_service.create_item(
        db_session,
        _knowledge_payload(
            item_key="ch.waybill.format",
            title="瑞士 Speedaf 运单号格式与输错提醒",
            channel="website",
            language="zh",
            knowledge_kind="business_fact",
            fact_question="客户输入瑞士 Speedaf 运单号查不到怎么办？",
            fact_answer="请客户核对运单号是否完整；瑞士 Speedaf 运单号通常为 CH 开头，后接 12 位数字。在没有可信查单结果时，不得判断或编造物流状态。",
            fact_aliases_json=["CH运单号格式", "运单号查不到", "waybill not found", "wrong tracking number"],
            fact_status="approved",
            answer_mode="guided_answer",
            draft_body="瑞士 Speedaf 运单号通常为 CH 开头，后接 12 位数字。查不到时请客户核对单号，不得判断物流状态。",
            draft_normalized_text="瑞士 Speedaf 运单号 CH 12 位数字 运单号查不到 核对单号",
        ),
        admin,
    )
    knowledge_service.publish_item(db_session, item, admin, notes="publish")

    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="zh",
        body="CH1200000011425",
        tracking_number="CH1200000011425",
        tracking_fact_evidence_present=False,
    )

    knowledge = context["knowledge_context"]
    assert "运单号格式" in knowledge["retrieval_query"]
    assert knowledge["query_expansion_terms"]
    assert knowledge["hits"][0]["item_key"] == "ch.waybill.format"
    assert knowledge["hits"][0]["metadata"]["knowledge_kind"] == "business_fact"


def test_runtime_context_includes_persona_identity_context_without_description(db_session):
    admin = _user(db_session)
    profile = persona_service.create_profile(
        db_session,
        PersonaProfileCreate(
            profile_key="identity.website.zh",
            name="Identity Website Chinese",
            description=None,
            channel="website",
            language="zh",
            draft_summary="Identity contract only.",
            draft_content_json={
                "brand_name": "猴王山",
                "assistant_name": "悟空客服",
                "identity_statement": "我是猴王山的悟空客服，可以协助处理客户服务问题。",
                "capabilities": ["回答常见问题", "转人工"],
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

    identity = context["persona_context"]["identity_context"]
    assert context["persona_context"]["profile_key"] == "identity.website.zh"
    assert context["persona_context"]["content_json"]["brand_name"] == "猴王山"
    assert identity["brand_name"] == "猴王山"
    assert identity["assistant_name"] == "悟空客服"
    assert identity["identity_statement"] == "我是猴王山的悟空客服，可以协助处理客户服务问题。"
    assert identity["capabilities"] == ["回答常见问题", "转人工"]


def test_knowledge_runtime_v2_excludes_probe_knowledge_and_reports_trace(db_session):
    admin = _user(db_session)
    real = knowledge_service.create_item(
        db_session,
        _knowledge_payload(item_key="production.address", title="Address Change Policy", channel="website", priority=5, draft_body="客户可以在发出前申请改地址。", draft_normalized_text="客户 可以 发出 前 申请 改地址"),
        admin,
    )
    probe = knowledge_service.create_item(
        db_session,
        _knowledge_payload(item_key="probe.address", title="[PROBE] Switzerland Address Change Fee", channel="website", priority=1),
        admin,
    )
    probe.citation_metadata_json = {"probe_category": "static_kb_boundary", "seed_source": "probe_seed_v3"}
    knowledge_service.publish_item(db_session, real, admin, notes="publish")
    knowledge_service.publish_item(db_session, probe, admin, notes="publish")

    result = retrieve_knowledge(
        db_session,
        query="我想改地址",
        tenant_key="default",
        channel="website",
        audience_scope="customer",
        limit=5,
    )

    assert result.trace["retrieval"] == "hybrid_rag_v2"
    assert "structured_exact" in result.trace["retrieval_methods"] or "fts" in result.trace["retrieval_methods"]
    assert [hit.item_key for hit in result.hits] == ["production.address"]
    assert "[PROBE]" not in str(result.trace)


def test_knowledge_runtime_v2_fails_closed_when_vector_fallback_is_disabled(monkeypatch, db_session):
    admin = _user(db_session)
    item = knowledge_service.create_item(
        db_session,
        _knowledge_payload(
            item_key="production.vector.required",
            title="Address Change Policy",
            channel="website",
            priority=5,
            draft_body="Customers can change delivery address before dispatch only.",
            draft_normalized_text="change delivery address before dispatch",
        ),
        admin,
    )
    knowledge_service.publish_item(db_session, item, admin, notes="publish")

    monkeypatch.setattr(
        knowledge_runtime,
        "get_settings",
        lambda: SimpleNamespace(
            knowledge_embeddings_enabled=True,
            knowledge_embedding_provider="openai_compatible",
            knowledge_embedding_dim=1536,
            knowledge_embedding_model="text-embedding-3-small",
            knowledge_embedding_base_url="https://embedding.example/v1",
            knowledge_embedding_api_key="not-used",
            knowledge_embedding_api_key_file=None,
            knowledge_embedding_timeout_seconds=5,
            knowledge_vector_fallback_allowed=False,
        ),
    )
    monkeypatch.setattr(
        knowledge_runtime,
        "get_embedding_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("embedding_provider_unreachable")),
    )

    result = retrieve_knowledge(
        db_session,
        query="change delivery address",
        tenant_key="default",
        channel="website",
        audience_scope="customer",
        limit=5,
    )

    assert result.hits == []
    assert result.no_answer_reason == "vector_retrieval_unavailable"
    assert result.trace["evidence_selected"] == []
    assert result.trace["vector"]["fallback_allowed"] is False
    assert result.trace["vector"]["degraded_reason"] == "RuntimeError"
