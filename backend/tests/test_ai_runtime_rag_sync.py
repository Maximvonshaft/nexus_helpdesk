from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _core_models  # noqa: F401
from app.db import Base
from app.enums import UserRole
from app.models import User
from app.models_control_plane import KnowledgeItem
from app.schemas_control_plane import KnowledgeItemCreate
from app.services import knowledge_service
from app.services.ai_runtime_rag_sync import build_runtime_rag_sync_items, sync_runtime_rag


def _make_db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rag_sync.db'}", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return SessionLocal(), engine


def _admin(session) -> User:
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


def _publish(session, admin, **overrides) -> KnowledgeItem:
    data = {
        "item_key": "kb.visible",
        "title": "Visible KB",
        "summary": "Visible knowledge",
        "status": "draft",
        "source_type": "text",
        "knowledge_kind": "document",
        "channel": "website",
        "audience_scope": "customer",
        "language": "en",
        "priority": 100,
        "fact_status": "approved",
        "answer_mode": "guided_answer",
        "draft_body": "Customers can ask Speedaf support for delivery help.",
        "draft_normalized_text": "Customers can ask Speedaf support for delivery help.",
        "citation_metadata_json": {"source": "test", "customer_visible": True},
    }
    data.update(overrides)
    item = knowledge_service.create_item(session, KnowledgeItemCreate(**data), admin)
    knowledge_service.publish_item(session, item, admin, notes="publish")
    return item


def test_build_runtime_rag_sync_items_excludes_explicit_internal_knowledge(tmp_path):
    session, engine = _make_db_session(tmp_path)
    try:
        admin = _admin(session)
        visible = _publish(session, admin)
        _publish(
            session,
            admin,
            item_key="nexus.support.support.sop",
            title="Internal SOP",
            draft_body="Internal handling notes.",
            draft_normalized_text="Internal handling notes.",
            citation_metadata_json={"source": "support_agent", "customer_visible": False},
        )
        session.commit()

        items, skipped = build_runtime_rag_sync_items(session)

        assert skipped == 1
        assert len(items) == 1
        assert items[0].external_id.startswith("nexus:")
        assert len(items[0].external_id) == 38
        assert items[0].metadata["schema"] == "nexus.ai_runtime_rag_sync.v1"
        assert items[0].metadata["customer_visible"] is True
        assert items[0].metadata["source_external_id"] == f"nexus:{visible.item_key}:v1:c0"
        assert "Customers can ask Speedaf support" in items[0].text
    finally:
        session.close()
        engine.dispose()


def test_sync_runtime_rag_posts_stable_items_and_marks_citation_metadata(tmp_path, monkeypatch):
    session, engine = _make_db_session(tmp_path)
    try:
        admin = _admin(session)
        item = _publish(session, admin)
        token_file = tmp_path / "token"
        token_file.write_text("secret-token", encoding="utf-8")
        calls = []

        def fake_post(endpoint, payload, token, *, timeout_seconds):
            calls.append((endpoint, payload, token, timeout_seconds))
            return {"status": "ok", "count": len(payload["items"])}

        monkeypatch.setattr("app.services.ai_runtime_rag_sync._post_json", fake_post)

        result = sync_runtime_rag(
            session,
            base_url="http://ai-runtime.internal:18081",
            token_file=str(token_file),
            batch_size=1,
            timeout_seconds=12,
        )
        session.commit()

        assert result.ok is True
        assert result.upserted_chunks == 1
        assert calls[0][0] == "http://ai-runtime.internal:18081/rag/upsert"
        external_id = calls[0][1]["items"][0]["external_id"]
        assert external_id.startswith("nexus:")
        assert len(external_id) == 38
        assert calls[0][1]["items"][0]["metadata"]["item_key"] == item.item_key
        assert calls[0][1]["items"][0]["metadata"]["source_external_id"] == f"nexus:{item.item_key}:v1:c0"
        assert calls[0][2] == "secret-token"
        assert "secret-token" not in json.dumps(result.as_dict(), ensure_ascii=False)

        session.refresh(item)
        sync_meta = item.citation_metadata_json["ai_runtime_rag_sync"]
        assert sync_meta["status"] == "synced"
        assert sync_meta["chunk_count"] == 1
        assert sync_meta["external_ids"] == [external_id]
    finally:
        session.close()
        engine.dispose()
