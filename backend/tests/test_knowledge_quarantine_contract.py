from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models  # noqa: E402,F401
from app import models_control_plane  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.models_control_plane import KnowledgeItem  # noqa: E402
from app.services import knowledge_service  # noqa: E402
from app.services.knowledge_retrieval_service import (  # noqa: E402
    index_published_item,
    retrieve_published_chunks,
)


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


def _file_item(db_session, *, item_key: str, draft_text: str | None = None) -> KnowledgeItem:
    row = KnowledgeItem(
        item_key=item_key,
        title="Quarantine Contract",
        status="draft",
        source_type="file",
        knowledge_kind="document",
        tenant_id="default",
        brand_id="default",
        country_scope="GLOBAL",
        channel_scope="all",
        visibility="customer",
        shareability="customer_visible",
        authority_level="imported",
        risk_level="medium",
        audience_scope="customer",
        priority=100,
        parsing_status="unparsed",
        fact_status="draft",
        answer_mode="guided_answer",
        draft_body=draft_text,
        draft_normalized_text=draft_text,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _upload(content: bytes, *, filename: str = "policy.txt", mime_type: str = "text/plain") -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": mime_type}),
    )


def test_upload_persists_quarantine_before_parser(db_session, monkeypatch):
    item = _file_item(db_session, item_key="quarantine.order")
    upload = _upload(b"Approved delivery policy")
    events: list[str] = []

    def fake_save(file):  # noqa: ANN001
        events.append("persist")
        return SimpleNamespace(
            stored_name=file.filename,
            storage_key="quarantine/policy.txt",
            file_size=24,
            mime_type="text/plain",
        )

    def fake_parse(**_kwargs):
        events.append("parse")
        return "Approved delivery policy", "Approved delivery policy"

    monkeypatch.setattr(knowledge_service.file_service, "save_upload", fake_save)
    monkeypatch.setattr(knowledge_service, "parse_document_bytes", fake_parse)

    knowledge_service.upload_document(db_session, item, upload, SimpleNamespace(id=None))

    assert events[:2] == ["persist", "parse"]


def test_file_publish_fails_closed_without_approved_quarantine(db_session):
    item = _file_item(
        db_session,
        item_key="quarantine.publish.blocked",
        draft_text="Customer-visible policy that has not passed quarantine.",
    )

    with pytest.raises(HTTPException) as exc_info:
        knowledge_service.publish_item(db_session, item, SimpleNamespace(id=None), notes="must fail")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Knowledge quarantine approval required"
    assert item.published_version == 0
    assert item.indexed_version == 0


def test_unapproved_file_version_is_not_retrieval_eligible(db_session):
    item = _file_item(
        db_session,
        item_key="quarantine.retrieval.blocked",
        draft_text="Quarantine-only address change policy.",
    )
    item.status = "active"
    item.published_body = item.draft_body
    item.published_normalized_text = item.draft_normalized_text
    item.published_version = 1
    item.fact_status = "approved"
    index_published_item(db_session, item)
    db_session.flush()

    result = retrieve_published_chunks(
        db_session,
        q="address change policy",
        audience_scope="customer",
        limit=5,
    )

    assert result.total == 0
    assert result.hits == []
