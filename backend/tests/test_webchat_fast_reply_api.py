from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_reply_api_tests.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from app import models_control_plane  # noqa: F401
from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import Customer, Ticket, User, WebchatRateLimitBucket
from app.models_control_plane import KnowledgeChunk, KnowledgeItem, KnowledgeItemVersion
from app.schemas_control_plane import KnowledgeItemCreate
from app.services import knowledge_service
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_config import get_webchat_fast_settings
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.schemas import ProviderResult
from app.webchat_models import WebchatConversation, WebchatMessage

BACKEND_ROOT = Path(__file__).resolve().parents[1]
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


def _payload(
    client_message_id: str = "client-1",
    *,
    session_id: str = "session-1",
    channel_key: str = "website",
    body: str = "Hi",
    recent_context: list[dict] | None = None,
) -> dict:
    return {
        "tenant_key": "default",
        "channel_key": channel_key,
        "session_id": session_id,
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": recent_context or [],
    }


def _ok_reply(text: str = "Hi, this is Speedy.", *, handoff: bool = False, tracking: str | None = None) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="openclaw_responses",
        reply=text,
        intent="tracking_lookup" if tracking else "greeting",
        tracking_number=tracking,
        handoff_required=handoff,
        handoff_reason="manual_review_required" if handoff else None,
        recommended_agent_action="Review shipment and reply." if handoff else None,
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


class _BusinessSlaAdapter(ProviderAdapter):
    name = "codex_app_server"

    def __init__(self, answer: str):
        self.answer = answer

    async def generate(self, db, request):
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=12,
            structured_output={
                "customer_reply": self.answer,
                "language": "zh",
                "intent": "other",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            },
            raw_payload_safe_summary={"safe": True},
        )


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
        _admin_user(db),
    )
    knowledge_service.publish_item(db, item, _admin_user(db), notes="api acceptance")


def _ensure_provider_runtime_route(db) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS provider_runtime_audit_logs (
            id VARCHAR(36) PRIMARY KEY,
            tenant_id VARCHAR(36) NOT NULL,
            provider VARCHAR(100) NOT NULL,
            credential_id VARCHAR(36),
            request_id VARCHAR(100) NOT NULL,
            channel_key VARCHAR(100) NOT NULL,
            session_id VARCHAR(100),
            operation VARCHAR(50) NOT NULL,
            status VARCHAR(50) NOT NULL,
            safe_summary JSON,
            error_code VARCHAR(255),
            elapsed_ms INTEGER,
            created_at DATETIME NOT NULL
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS provider_routing_rules (
            id VARCHAR(36) PRIMARY KEY,
            tenant_id VARCHAR(36) NOT NULL,
            channel_key VARCHAR(100) NOT NULL,
            scenario VARCHAR(100) NOT NULL,
            primary_provider VARCHAR(100) NOT NULL,
            fallback_providers JSON,
            output_contract VARCHAR(100) NOT NULL,
            timeout_ms INTEGER NOT NULL,
            canary_percent INTEGER NOT NULL DEFAULT 0,
            kill_switch BOOLEAN NOT NULL DEFAULT 0,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """))
    db.execute(text("""
        DELETE FROM provider_routing_rules
        WHERE tenant_id = 'default' AND channel_key = 'website' AND scenario = 'webchat_fast_reply'
    """))
    db.execute(text("""
        INSERT INTO provider_routing_rules (
            id, tenant_id, channel_key, scenario, primary_provider, fallback_providers,
            output_contract, timeout_ms, canary_percent, kill_switch, enabled, created_at, updated_at
        )
        VALUES (
            :id, 'default', 'website', 'webchat_fast_reply', 'codex_app_server', '[]',
            'speedaf_webchat_fast_reply_v1', 10000, 100, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
    """), {"id": str(uuid4())})


def test_fast_reply_same_session_reuses_conversation_and_uses_server_context(monkeypatch):
    seen_contexts: list[list[dict]] = []

    async def fake_generate(**kwargs):
        seen_contexts.append(kwargs["recent_context"])
        if kwargs["body"] == "Where is my parcel?":
            return _ok_reply("Please provide your tracking number.")
        return _ok_reply("I can see this is the tracking number for your parcel inquiry.", tracking="SPX123456789CH")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    first = client.post("/api/webchat/fast-reply", json=_payload("msg-0001", body="Where is my parcel?"))
    second = client.post("/api/webchat/fast-reply", json=_payload("msg-0002", body="SPX123456789CH"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(seen_contexts) == 2
    assert any(item["text"] == "Where is my parcel?" for item in seen_contexts[1])
    assert any("tracking number" in item["text"].lower() for item in seen_contexts[1])

    db = SessionLocal()
    try:
        conversations = db.execute(select(WebchatConversation).where(WebchatConversation.fast_session_id == "session-1")).scalars().all()
        assert len(conversations) == 1
        messages = db.execute(select(WebchatMessage).where(WebchatMessage.conversation_id == conversations[0].id)).scalars().all()
        assert len(messages) == 4
        assert [m.direction for m in messages].count("visitor") == 2
        assert [m.direction for m in messages].count("ai") == 2
    finally:
        db.close()


def test_fast_reply_provider_runtime_unavailable_returns_controlled_non_500(monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    get_webchat_fast_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route",
        AsyncMock(
            return_value=ProviderResult(
                ok=False,
                provider="provider_runtime",
                elapsed_ms=13,
                error_code="openclaw_responses_unavailable",
                structured_output=None,
                raw_payload_safe_summary={"safe": True},
            )
        ),
    )

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload(
            "provider-runtime-unavailable",
            session_id="provider-runtime-unavailable-session",
            body="Hello, I need help.",
        ),
        headers={"Origin": "http://localhost"},
    )

    get_webchat_fast_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "openclaw_responses_unavailable"


def test_fast_reply_provider_runtime_returns_published_business_sla_direct_answer(monkeypatch):
    answer = "瑞士海运时效为 15 天。"
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    get_webchat_fast_settings.cache_clear()

    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: _BusinessSlaAdapter(answer))

    db = SessionLocal()
    try:
        _seed_shipping_sla_fact(db, item_key="fact.ch.shipping-sla.api", answer=answer)
        _ensure_provider_runtime_route(db)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload(
            "provider-runtime-shipping-sla",
            session_id="provider-runtime-shipping-sla-session",
            body="瑞士海运时效是多少？",
        ),
        headers={"Origin": "http://localhost"},
    )

    get_webchat_fast_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["reply_source"] != "server_handoff_policy"
    assert "15" in payload["reply"]
    assert payload["grounding_applied"] is True
    assert payload["reply_source"] == "codex_app_server"
    assert payload["grounding_reason"] == "locked_fact_ai_grounded"
    assert payload["grounding_source"]["item_key"] == "fact.ch.shipping-sla.api"
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
        assert metadata["grounding_applied"] is True
        assert metadata["grounding_reason"] == "locked_fact_ai_grounded"
        assert metadata["grounding_source"]["item_key"] == "fact.ch.shipping-sla.api"
    finally:
        db.close()


def test_fast_handoff_same_session_does_not_create_duplicate_ticket():
    for idx in range(3):
        response = client.post(
            "/api/webchat/fast-reply",
            json=_payload(f"lost-{idx}", body="My parcel is lost SPX123456789CH"),
        )
        assert response.status_code == 200
        assert response.json()["handoff_required"] is True

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 1
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
        conversation = db.execute(select(WebchatConversation)).scalar_one()
        assert conversation.ticket_id is not None
        assert db.execute(select(func.count(WebchatMessage.id)).where(WebchatMessage.conversation_id == conversation.id)).scalar_one() >= 5
    finally:
        db.close()


def test_fast_handoff_same_tracking_number_reuses_ticket_across_sessions():
    first = client.post("/api/webchat/fast-reply", json=_payload("msg-a001", session_id="session-a", body="My parcel is lost SPX123456789CH"))
    second = client.post("/api/webchat/fast-reply", json=_payload("msg-b001", session_id="session-b", body="My parcel is lost SPX123456789CH"))
    assert first.status_code == 200
    assert second.status_code == 200

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
    finally:
        db.close()


def test_fast_customer_external_ref_is_channel_scoped_for_same_session():
    first = client.post(
        "/api/webchat/fast-reply",
        json=_payload(
            "channel-customer-1",
            session_id="shared-browser-session",
            channel_key="website",
            body="My parcel is lost WEB111111111CH",
        ),
    )
    second = client.post(
        "/api/webchat/fast-reply",
        json=_payload(
            "channel-customer-2",
            session_id="shared-browser-session",
            channel_key="mobile",
            body="My parcel is lost MOB222222222CH",
        ),
    )
    assert first.status_code == 200
    assert second.status_code == 200

    db = SessionLocal()
    try:
        customers = db.execute(select(Customer).order_by(Customer.external_ref.asc())).scalars().all()
        assert [customer.external_ref for customer in customers] == [
            "webchat-fast:default:mobile:shared-browser-session",
            "webchat-fast:default:website:shared-browser-session",
        ]
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 2
    finally:
        db.close()


def test_fast_reply_idempotency_same_client_message_id_returns_cached_response(monkeypatch):
    calls = {"generate": 0}

    async def fake_generate(**kwargs):
        calls["generate"] += 1
        return _ok_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    first = client.post("/api/webchat/fast-reply", json=_payload("same-msg"))
    second = client.post("/api/webchat/fast-reply", json=_payload("same-msg"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert calls["generate"] == 1
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatMessage.id))).scalar_one() == 2
    finally:
        db.close()


def test_fast_reply_different_session_creates_different_conversation(monkeypatch):
    async def fake_generate(**kwargs):
        return _ok_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    assert client.post("/api/webchat/fast-reply", json=_payload("msg-1001", session_id="session-a")).status_code == 200
    assert client.post("/api/webchat/fast-reply", json=_payload("msg-1002", session_id="session-b")).status_code == 200
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
    finally:
        db.close()


def test_demo_payload_does_not_force_empty_context():
    source = (BACKEND_ROOT / "app/static/webchat/demo/js/app.js").read_text(encoding="utf-8")
    assert "recent_context: []" not in source
    assert "sessionStorage" in source
    assert "recentContext.slice" in source


def test_widget_persists_recent_context():
    source = (BACKEND_ROOT / "app/static/webchat/widget.js").read_text(encoding="utf-8")
    assert "contextKey" in source
    assert "sessionStorage.setItem(contextKey" in source
    assert "function buildApiRecentContext()" in source
    assert "recent_context: buildApiRecentContext()" in source
    assert "recent_context: state.recentContext" not in source


def test_fast_rate_limit_still_blocks_rotated_sessions(monkeypatch):
    reset_webchat_fast_rate_limit_for_tests()
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", "60")

    async def fake_generate(**kwargs):
        return _ok_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    headers = {"User-Agent": "pytest-fast-limit/1.0"}
    first = client.post("/api/webchat/fast-reply", json=_payload("rl-1", session_id="session-a"), headers=headers)
    second = client.post("/api/webchat/fast-reply", json=_payload("rl-2", session_id="session-b"), headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatRateLimitBucket.id))).scalar_one() == 1
    finally:
        db.close()
