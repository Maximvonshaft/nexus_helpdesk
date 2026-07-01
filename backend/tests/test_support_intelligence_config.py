import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models  # noqa: F401,E402
from app import models_control_plane  # noqa: F401,E402
from app.api.support_intelligence import (  # noqa: E402
    StatusDictionaryEntryRequest,
    StatusDictionaryWriteRequest,
    _ensure_can_manage_support_intelligence,
    _ensure_can_publish_support_intelligence,
    _ensure_can_read_support_intelligence,
    get_status_dictionary as api_get_status_dictionary,
    publish_status_dictionary as api_publish_status_dictionary,
    save_status_dictionary_draft as api_save_status_dictionary_draft,
)
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import AIConfigResource, AIConfigVersion  # noqa: E402
from app.models_control_plane import KnowledgeItem, PersonaProfile  # noqa: E402
from app.services.support_intelligence_service import (  # noqa: E402
    STATUS_DICTIONARY_RESOURCE_KEY,
    build_support_intelligence_config,
    get_status_dictionary_bundle,
    publish_status_dictionary,
    save_status_dictionary_draft,
)


class FakeBridge:
    def support_knowledge_config(self, payload):
        if payload == {"operation": "card-list"}:
            return {
                "ok": True,
                "cards": [
                    {
                        "id": "delivered-but-not-received",
                        "title": "Delivered but not received",
                        "country": "CH",
                        "status": "published",
                        "customer_visible": True,
                        "ai_enabled": True,
                        "runtime_scope": "customer_answer",
                        "owner": "support-ops",
                        "workspace_path": "knowledge/runtime/customer_kb/shipment-exception-delivered-but-not-received.md",
                        "customer_answer": "We can help check the delivery proof.",
                    }
                ],
            }
        if payload == {"operation": "status-dictionary-list"}:
            raise AssertionError("status dictionary must be loaded from Nexus DB, not the legacy bridge")
        raise AssertionError(payload)


class BrokenBridge:
    def support_knowledge_config(self, payload):
        raise RuntimeError("runtime_config_source_unavailable")


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, SessionLocal()


def test_support_intelligence_config_merges_runtime_cards_and_config_library():
    engine, session = _session()
    try:
        session.add(
            PersonaProfile(
                profile_key="support.whatsapp.de",
                name="WhatsApp German Support",
                channel="whatsapp",
                language="de",
                is_active=True,
                draft_summary="Friendly and concise",
                published_summary="Friendly and concise",
                published_version=1,
            )
        )
        session.add(
            KnowledgeItem(
                item_key="support.delivery.delay",
                title="Delivery delay FAQ",
                status="published",
                channel="whatsapp",
                audience_scope="customer",
                priority=80,
                published_version=1,
                published_body="Delay guidance",
            )
        )
        publish_status_dictionary(
            session,
            [
                {
                    "code": "3750",
                    "label": "运输中",
                    "desc": "包裹正在运输途中",
                    "action": "请耐心等待后续更新",
                }
            ],
            SimpleNamespace(id=7, username="ops"),
        )
        session.commit()

        result = build_support_intelligence_config(session, bridge_client=FakeBridge())

        assert result["bridge_status"]["ok"] is True
        assert result["config_library"]["counts"]["personas"] == 1
        assert result["config_library"]["counts"]["knowledge_items"] == 1
        assert result["runtime_knowledge_cards"][0]["enabled"] is True
        assert result["status_dictionary_status"]["source"] == "nexus_ai_config_resources"
        assert result["status_dictionary_status"]["published_count"] == 1
        assert result["status_dictionary_entries"][0]["code"] == "3750"
        assert result["status_dictionary_entries"][0]["published_label"] == "运输中"
        assert result["areas"][1]["runtime_effective_count"] == 1
        assert next(area for area in result["areas"] if area["key"] == "status_dictionary")["runtime_effective_count"] == 1
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_status_dictionary_draft_and_publish_are_db_backed():
    engine, session = _session()
    actor = SimpleNamespace(id=7, username="ops")
    try:
        draft = save_status_dictionary_draft(
            session,
            [
                {
                    "code": " 3750 ",
                    "label": "运输中",
                    "desc": "包裹正在运输途中",
                    "action": "请耐心等待后续更新",
                    "language_labels": {"ZH": "运输中", "": "ignored"},
                    "promise_eta": True,
                }
            ],
            actor,
        )

        assert draft["status"] == "draft"
        assert draft["source"] == "nexus_ai_config_resources"
        assert draft["entries"][0]["code"] == "3750"
        assert draft["entries"][0]["status"] == "draft"
        assert draft["entries"][0]["language_labels"] == {"zh": "运输中"}

        updated = save_status_dictionary_draft(
            session,
            [{"code": "3750", "label": "运输中", "desc": "已更新", "needs_human": True}],
            actor,
        )
        assert updated["draft_count"] == 1
        assert updated["entries"][0]["desc"] == "已更新"
        assert updated["entries"][0]["needs_human"] is True

        published = publish_status_dictionary(session, None, actor)

        assert published["status"] == "ready"
        assert published["published_count"] == 1
        assert published["published_version"] == 1
        assert published["entries"][0]["status"] == "published"
        assert published["entries"][0]["published_desc"] == "已更新"

        row = session.query(AIConfigResource).filter(AIConfigResource.resource_key == STATUS_DICTIONARY_RESOURCE_KEY).one()
        assert row.config_type == "status_dictionary"
        assert row.published_content_json["entries"][0]["code"] == "3750"
        assert session.query(AIConfigVersion).filter(AIConfigVersion.resource_id == row.id).count() == 1

        bundle = get_status_dictionary_bundle(session)
        assert bundle["resource_id"] == row.id
        assert bundle["message"].startswith("Status dictionary is stored in Nexus DB")
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_status_dictionary_api_routes_persist_to_db():
    engine, session = _session()
    admin = SimpleNamespace(id=2, username="admin", role=UserRole.admin)
    try:
        draft = api_save_status_dictionary_draft(
            StatusDictionaryWriteRequest(
                entry=StatusDictionaryEntryRequest(
                    code="9010",
                    label="清关中",
                    desc="包裹正在等待清关",
                    action="请等待清关更新",
                    needs_human=False,
                )
            ),
            db=session,
            current_user=admin,
        )

        assert draft["status"] == "draft"
        assert draft["entries"][0]["code"] == "9010"

        published = api_publish_status_dictionary(
            StatusDictionaryWriteRequest(),
            db=session,
            current_user=admin,
        )
        listed = api_get_status_dictionary(db=session, current_user=admin)

        assert published["status"] == "ready"
        assert listed["published_version"] == 1
        assert listed["entries"][0]["published_label"] == "清关中"
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_support_intelligence_config_marks_runtime_bridge_degraded():
    engine, session = _session()
    try:
        result = build_support_intelligence_config(session, bridge_client=BrokenBridge())

        assert result["bridge_status"]["ok"] is False
        assert result["runtime_knowledge_cards"] == []
        assert result["status_dictionary_status"]["source"] == "nexus_ai_config_resources"
        assert any("运行知识桥不可用" in item for item in result["gaps"])
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_status_dictionary_publish_requires_config_management_capability():
    agent = SimpleNamespace(id=1, role=UserRole.agent)
    admin = SimpleNamespace(id=2, role=UserRole.admin)
    manager = SimpleNamespace(id=3, role=UserRole.manager)

    _ensure_can_read_support_intelligence(agent, None)
    _ensure_can_manage_support_intelligence(admin, None)
    _ensure_can_publish_support_intelligence(admin, None)

    with pytest.raises(HTTPException) as exc_info:
        _ensure_can_manage_support_intelligence(agent, None)

    assert getattr(exc_info.value, "status_code", None) == 403
    assert getattr(exc_info.value, "detail", "") == "support_intelligence_requires_config_management_capability"

    with pytest.raises(HTTPException) as publish_exc:
        _ensure_can_publish_support_intelligence(manager, None)

    assert getattr(publish_exc.value, "status_code", None) == 403
    assert getattr(publish_exc.value, "detail", "") == "support_intelligence_requires_runtime_publish_capability"


def test_support_intelligence_status_dictionary_api_uses_db_not_retired_bridge():
    source = (ROOT / "app" / "api" / "support_intelligence.py").read_text(encoding="utf-8")

    assert "_bridge_status_dictionary" not in source
    assert "legacy_status_dictionary_runtime_bridge_retired" not in source
    assert "get_status_dictionary_bundle" in source
    assert "save_status_dictionary_draft_bundle" in source
    assert "publish_status_dictionary_bundle" in source
