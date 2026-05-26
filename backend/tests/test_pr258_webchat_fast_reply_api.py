from __future__ import annotations

import json
import os
from uuid import uuid4
from unittest.mock import AsyncMock

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_reply_api_tests.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app import models_control_plane  # noqa: F401
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import Customer, Ticket, User, WebchatRateLimitBucket
from app.models_control_plane import KnowledgeChunk, KnowledgeItem, KnowledgeItemVersion
from app.schemas_control_plane import KnowledgeItemCreate
from app.services import knowledge_service
from app.services.webchat_fast_config import get_webchat_fast_settings
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests
from app.webchat_models import WebchatConversation, WebchatMessage

client = TestClient(app)


def setup_function():
    db = SessionLocal()
    try:
        db.execute(text("DROP TABLE IF EXISTS webchat_rate_limits"))
        db.execute(text("DROP TABLE IF EXISTS knowledge_chunks"))
        db.execute(text("DROP TABLE IF EXISTS knowledge_item_versions"))
        db.execute(text("DROP TABLE IF EXISTS knowledge_items"))
        db.commit()
    finally:
        db.close()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.execute(delete(WebchatMessage))
        db.execute(delete(WebchatConversation))
        db.execute(delete(Ticket))
        db.execute(delete(Customer))
        db.commit()
    finally:
        db.close()
    reset_webchat_fast_rate_limit_for_tests()


def _payload(*, body: str, session_id: str = "session-pr258-api") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": session_id,
        "client_message_id": f"msg-{uuid4().hex}",
        "body": body,
        "recent_context": [],
    }


def _admin_user(db) -> User:
    user = db.execute(select(User).where(User.username == "api-rag-admin")).scalar_one_or_none()
    if user:
        return user
    user = User(
        username="api-rag-admin",
        display_name="API RAG Admin",
        email=f"api-rag-admin-{uuid4().hex}@example.test",
        password_hash="not-a-real-password-hash",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _seed_shipping_sla_fact(db, *, item_key: str, answer: str) -> None:
    existing = db.execute(select(KnowledgeItem).where(KnowledgeItem.item_key == item_key)).scalar_one_or_none()
    if existing:
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.item_id == existing.id))
        db.execute(delete(KnowledgeItemVersion).where(KnowledgeItemVersion.item_id == existing.id))
        db.delete(existing)
        db.flush()
    user = _admin_user(db)
    item = knowledge_service.create_item(
        db,
        KnowledgeItemCreate(
            item_key=item_key,
            title="瑞士海运时效",
            summary="瑞士海运 SLA",
            status="draft",
            source_type="text",
            knowledge_kind="business_fact",
            channel="website",
            audience_scope="customer",
            language="zh",
            priority=10,
            fact_question="瑞士海运时效是多少？",
            fact_answer=answer,
            fact_aliases_json=["瑞士海运多久", "瑞士海运时效"],
            fact_status="approved",
            answer_mode="direct_answer",
        ),
        user,
    )
    knowledge_service.publish_item(db, item, user, notes="pr258 api acceptance")


def test_fast_reply_returns_approved_direct_answer_when_provider_runtime_unavailable(monkeypatch):
    answer = "瑞士海运时效为 15 天。"
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    get_webchat_fast_settings.cache_clear()

    db = SessionLocal()
    try:
        _seed_shipping_sla_fact(db, item_key="fact.ch.shipping-sla.pr258.api", answer=answer)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route",
        AsyncMock(side_effect=AssertionError("provider should be bypassed for approved direct_answer")),
    )

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload(body="瑞士海运时效是多少？"),
        headers={"Origin": "http://localhost"},
    )

    get_webchat_fast_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["reply"] == answer
    assert payload["reply_source"] == "knowledge_direct_answer:grounded_knowledge"
    assert payload["grounding_applied"] is True
    assert payload["grounding_reason"] == "approved_direct_answer_override"
    assert payload["grounding_source"]["item_key"] == "fact.ch.shipping-sla.pr258.api"
    assert payload.get("error_code") not in {"all_providers_failed", "parse_reject"}

    db = SessionLocal()
    try:
        message = db.execute(
            select(WebchatMessage).where(
                WebchatMessage.direction == "ai",
                WebchatMessage.body == answer,
            )
        ).scalar_one()
        metadata = json.loads(message.metadata_json or "{}")
        assert metadata["reply_source"] == "knowledge_direct_answer:grounded_knowledge"
        assert metadata["grounding_applied"] is True
        assert metadata["grounding_reason"] == "approved_direct_answer_override"
        assert metadata["grounding_source"]["item_key"] == "fact.ch.shipping-sla.pr258.api"
    finally:
        db.close()
