from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/realtime_health_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402


def _headers(user_id: int = 9701) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def setup_module() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        users = [
            User(id=9701, username="realtime_admin", display_name="Realtime Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9702, username="realtime_agent", display_name="Realtime Agent", password_hash="test", role=UserRole.agent, is_active=True),
        ]
        for user in users:
            existing = db.query(User).filter(User.id == user.id).first()
            if existing is None:
                db.add(user)
            else:
                existing.username = user.username
                existing.display_name = user.display_name
                existing.role = user.role
                existing.is_active = True
        db.commit()
    finally:
        db.close()


def test_realtime_health_requires_runtime_manage_capability() -> None:
    client = TestClient(app)

    response = client.get("/api/admin/realtime-health", headers=_headers(9702))

    assert response.status_code == 403
    assert "participant_token" not in response.text
    assert "visitor_token" not in response.text


def test_realtime_health_returns_backend_runtime_contract() -> None:
    client = TestClient(app)

    response = client.get("/api/admin/realtime-health", headers=_headers(9701))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"disabled", "ready", "degraded"}
    assert payload["features"]["broker"] in {"database", "memory"}
    assert isinstance(payload["features"]["fallback_poll_ms"], int)
    assert isinstance(payload["features"]["heartbeat_ms"], int)
    assert isinstance(payload["connections"]["connections"], int)
    assert isinstance(payload["replay"]["events_last_5m"], int)
    assert "auth_failures_total" in payload["observability"]
    assert "participant_token" not in response.text
    assert "visitor_token" not in response.text
