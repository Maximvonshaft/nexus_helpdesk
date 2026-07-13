from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_demo_status_safety_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.api import admin_provider_runtime as admin_provider_runtime_module
from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import User
from app.services.webcall_ai import demo_lab as demo_lab_module
from app.services.webchat_runtime_config import get_webchat_runtime_settings


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    for key in [
        "WEBCALL_AI_DEMO_LAB_ENABLED",
        "WEBCALL_AI_DEMO_LAB_KILL_SWITCH",
        "WEBCALL_AI_DEMO_LAB_MODE",
        "WEBCHAT_VOICE_ENABLED",
        "WEBCHAT_VOICE_PROVIDER",
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
    ]:
        monkeypatch.delenv(key, raising=False)
    get_webchat_runtime_settings.cache_clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.add(
            User(
                id=9611,
                username="demo_status_admin",
                display_name="Demo Status Admin",
                password_hash="x",
                role=UserRole.admin,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)
    get_webchat_runtime_settings.cache_clear()


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(9611)}"}


def test_demo_configuration_exception_is_bounded_on_direct_and_embedded_status(monkeypatch):
    sensitive_exception = "RuntimeError token=TOP-SECRET customer=private@example.test"

    def raise_sensitive_runtime_error():
        raise RuntimeError(sensitive_exception)

    monkeypatch.setattr(
        demo_lab_module,
        "get_webcall_ai_demo_lab_settings",
        raise_sensitive_runtime_error,
    )
    monkeypatch.setattr(
        admin_provider_runtime_module,
        "_traffic_routing_rules",
        lambda db: {
            "status": "ready",
            "reason_code": None,
            "items": [],
            "truncated": False,
        },
    )
    client = TestClient(app)

    direct = client.get("/api/admin/webcall-ai-demo/status", headers=_headers())
    embedded = client.get("/api/admin/provider-runtime/status", headers=_headers())

    assert direct.status_code == 200, direct.text
    assert embedded.status_code == 200, embedded.text
    assert direct.json()["blockers"] == ["webcall_ai_demo_lab_configuration_invalid"]
    assert embedded.json()["webcall_ai_demo_lab"]["blockers"] == [
        "webcall_ai_demo_lab_configuration_invalid"
    ]
    assert sensitive_exception not in direct.text
    assert sensitive_exception not in embedded.text
    assert "RuntimeError" not in direct.text
    assert "RuntimeError" not in embedded.text
