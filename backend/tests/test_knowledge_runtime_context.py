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
from app.enums import ConversationState, TicketPriority, TicketSource, TicketStatus, UserRole, SourceChannel, ResolutionCategory  # noqa: E402
from app.models import Market, Ticket, User  # noqa: E402
from app.schemas_control_plane import KnowledgeItemCreate, KnowledgePublishRequest, PersonaProfileCreate, PersonaPublishRequest  # noqa: E402
from app.services import knowledge_service, persona_service  # noqa: E402
from app.services.ai_runtime_context import build_agent_context  # noqa: E402
from app.services.knowledge_retrieval_service import search_published_chunks  # noqa: E402
from app.services.knowledge_runtime import runtime as knowledge_runtime  # noqa: E402
from app.services.knowledge_runtime import retrieve_knowledge  # noqa: E402


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


def test_runtime_context_projects_persona_and_channel_without_pre_model_retrieval(db_session):
    admin = _user(db_session)
    profile = persona_service.create_profile(
        db_session,
        PersonaProfileCreate(
            profile_key="default.website.en",
            name="Default Website English",
            channel="website",
            language="en",
            draft_summary="Use approved Skills and Tools.",
            draft_content_json={"tone": "concise"},
        ),
        admin,
    )
    persona_service.publish_profile(db_session, profile, admin, notes="publish")
    item = knowledge_service.create_item(
        db_session,
        _knowledge_payload(item_key="runtime.address", channel="website"),
        admin,
    )
    knowledge_service.publish_item(db_session, item, admin, notes="publish")

    context = build_agent_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Can I change my delivery address?",
    )

    assert context["context_version"] == "nexus.agent_context.v1"
    assert context["persona_context"]["profile_key"] == "default.website.en"
    assert context["persona_context"]["identity_context"]
    assert context["channel_context"]["channel"] == "website"
    assert "knowledge_context" not in context
    assert "rag_trace" not in context
    assert "safety_policy" not in context
    assert "conversation_state" not in context

    hits, total = search_published_chunks(
        db_session,
        q="change delivery address",
        channel="website",
        audience_scope="customer",
        limit=5,
    )
    assert total == 1
    assert [hit.item_key for hit in hits] == ["runtime.address"]


def test_runtime_context_uses_effective_country_in_generic_channel_context(db_session):
    ch_market = Market(
        code="CH",
        name="Switzerland",
        country_code="CH",
        is_active=True,
    )
    db_session.add(ch_market)
    db_session.flush()
    ticket = Ticket(
        ticket_no="T-CH-GENERIC-CONTEXT",
        title="Swiss customer",
        description="Swiss customer",
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        resolution_category=ResolutionCategory.none,
        market_id=ch_market.id,
        conversation_state=ConversationState.ai_active,
    )
    db_session.add(ticket)
    db_session.flush()

    context = build_agent_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Please help with the current request.",
        market_id=ch_market.id,
        ticket=ticket,
        channel_payload={"order_destination_country": "CH"},
    )

    channel = context["channel_context"]
    assert channel["effective_country"] == "CH"
    assert channel["country_source"] == "order_destination_country"
    assert "knowledge_context" not in context


def test_runtime_context_has_no_retired_tracking_prefetch_parameters(db_session):
    import inspect

    signature = inspect.signature(build_agent_context)
    assert "tracking_number" not in signature.parameters
    assert "tracking_fact_evidence_present" not in signature.parameters
    assert not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    context = build_agent_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Reference CH1200000011425",
    )
    serialized = str(context)
    assert "knowledge_context" not in context
    assert "conversation_state" not in context
    assert "tracking_fact_evidence_present" not in serialized
    assert "locked_facts" not in serialized
