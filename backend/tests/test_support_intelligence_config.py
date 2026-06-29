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
from app.api.support_intelligence import _ensure_can_manage_support_intelligence, _ensure_can_publish_support_intelligence, _ensure_can_read_support_intelligence  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import AIConfigResource  # noqa: E402
from app.models_control_plane import KnowledgeItem, PersonaProfile  # noqa: E402
from app.services.openclaw_client_factory import OpenClawBridgeHTTPError  # noqa: E402
from app.services.support_intelligence_service import build_support_intelligence_config  # noqa: E402


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
            return {
                "ok": True,
                "entries": [
                    {
                        "code": "3750",
                        "label": "运输中",
                        "desc": "包裹正在运输途中",
                        "action": "请耐心等待后续更新",
                        "status": "published",
                        "editable": True,
                    }
                ],
                "published_version": 1,
            }
        raise AssertionError(payload)


class BrokenBridge:
    def support_knowledge_config(self, payload):
        raise OpenClawBridgeHTTPError("bridge_http_503")


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
        session.add(
            AIConfigResource(
                resource_key="support.status.dictionary",
                config_type="status_dictionary",
                name="Support status dictionary",
                scope_type="channel",
                scope_value="whatsapp",
                is_active=True,
                published_version=1,
                published_summary="Status labels",
            )
        )
        session.commit()

        result = build_support_intelligence_config(session, bridge_client=FakeBridge())

        assert result["bridge_status"]["ok"] is True
        assert result["config_library"]["counts"]["personas"] == 1
        assert result["config_library"]["counts"]["knowledge_items"] == 1
        assert result["runtime_knowledge_cards"][0]["enabled"] is True
        assert result["status_dictionary_entries"][0]["code"] == "3750"
        assert result["areas"][1]["runtime_effective_count"] == 1
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
