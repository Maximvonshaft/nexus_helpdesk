import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models  # noqa: F401,E402
from app import models_control_plane  # noqa: F401,E402
from app.api.support_intelligence import (  # noqa: E402
    _ensure_can_manage_support_intelligence,
    _ensure_can_publish_support_intelligence,
    _ensure_can_read_support_intelligence,
)
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import AIConfigResource  # noqa: E402
from app.models_control_plane import KnowledgeItem, PersonaProfile  # noqa: E402
from app.services.support_intelligence_service import build_support_intelligence_config  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, SessionLocal()


def test_support_intelligence_uses_only_canonical_control_plane():
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
                resource_key="support.rules",
                config_type="rule",
                name="Support rules",
                scope_type="channel",
                scope_value="whatsapp",
                is_active=True,
                published_version=1,
                published_summary="Rules",
            )
        )
        session.commit()

        result = build_support_intelligence_config(session)

        assert "bridge_status" not in result
        assert "runtime_knowledge_cards" not in result
        assert "status_dictionary_status" not in result
        assert result["bundle"]["mode"] == "canonical_control_plane"
        assert result["runtime_status"]["authority"] == "nexus_control_plane"
        assert result["runtime_status"]["external_runtime_bridge"] is False
        assert result["config_library"]["counts"]["personas"] == 1
        assert result["config_library"]["counts"]["knowledge_items"] == 1
        assert "bridge_client" not in inspect.signature(build_support_intelligence_config).parameters
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_status_dictionary_bridge_routes_are_absent():
    source = (ROOT / "app/api/support_intelligence.py").read_text(encoding="utf-8")
    assert "status-dictionary" not in source
    assert "_bridge_status_dictionary" not in source


def test_support_intelligence_capabilities_are_enforced():
    agent = SimpleNamespace(id=1, role=UserRole.agent)
    admin = SimpleNamespace(id=2, role=UserRole.admin)
    manager = SimpleNamespace(id=3, role=UserRole.manager)

    _ensure_can_read_support_intelligence(agent, None)
    _ensure_can_manage_support_intelligence(admin, None)
    _ensure_can_publish_support_intelligence(admin, None)

    with pytest.raises(HTTPException) as exc_info:
        _ensure_can_manage_support_intelligence(agent, None)
    assert getattr(exc_info.value, "status_code", None) == 403

    with pytest.raises(HTTPException) as publish_exc:
        _ensure_can_publish_support_intelligence(manager, None)
    assert getattr(publish_exc.value, "status_code", None) == 403
