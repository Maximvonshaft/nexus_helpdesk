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
from app.schemas_control_plane import KnowledgeItemCreate, KnowledgePublishRequest, PersonaProfileCreate, PersonaPublishRequest  # noqa: E402
from app.services import knowledge_service, persona_service  # noqa: E402
from app.services.ai_runtime_context import build_webchat_runtime_context  # noqa: E402
from app.services.knowledge_retrieval_service import search_published_chunks  # noqa: E402


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
    assert context["knowledge_context"]["hits"][0]["item_key"] == "runtime.address"
    assert context["safety_policy"]["knowledge_scope"] == "policy_sop_faq_only"
    assert "tracking_fact_evidence_present=true" in context["safety_policy"]["tracking_truth_boundary"]


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
