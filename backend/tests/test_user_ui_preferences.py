from __future__ import annotations

import os
import sys
from datetime import timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, User  # noqa: E402
from app.models_user_ui_preferences import UserUIPreference  # noqa: E402
from app.services.user_ui_preferences import normalize_ui_locale  # noqa: E402

PASSWORD = "Nexus!Locale2026"


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ui-locale.db'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    db_session = factory()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), db_session, engine


def _close(db_session, engine) -> None:
    app.dependency_overrides.pop(get_db, None)
    db_session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _database_neutral_timestamp(value):
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def test_ui_locale_aliases_are_normalized_case_insensitively() -> None:
    assert normalize_ui_locale("zh_CN") == "zh-CN"
    assert normalize_ui_locale("EN") == "en"
    assert normalize_ui_locale("de-CH") == "de"
    assert normalize_ui_locale("CNR") == "cnr"
    assert normalize_ui_locale("cnr-ME") == "cnr"
    assert normalize_ui_locale("sr-ME") == "cnr"
    assert normalize_ui_locale("SR-latn-me") == "cnr"


def test_ui_locale_is_persisted_audited_and_does_not_revoke_session(tmp_path):
    client, db_session, engine = _client(tmp_path)
    try:
        operator = User(
            username="locale_operator",
            display_name="Locale Operator",
            email="locale@example.test",
            password_hash=hash_password(PASSWORD),
            role=UserRole.agent,
            is_active=True,
        )
        db_session.add(operator)
        db_session.commit()
        identity_version = _database_neutral_timestamp(operator.updated_at)

        login = client.post(
            "/api/auth/login",
            json={"username": operator.username, "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        assert login.json()["user"]["ui_locale"] == "zh-CN"
        assert login.json()["user"]["ui_locale_configured"] is False
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        updated = client.patch(
            "/api/auth/preferences",
            headers=headers,
            json={"ui_locale": "EN"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json() == {"ui_locale": "en"}

        db_session.refresh(operator)
        assert _database_neutral_timestamp(operator.updated_at) == identity_version
        assert db_session.get(UserUIPreference, operator.id).ui_locale == "en"

        current = client.get("/api/auth/me", headers=headers)
        assert current.status_code == 200, current.text
        assert current.json()["ui_locale"] == "en"
        assert current.json()["ui_locale_configured"] is True

        audit = (
            db_session.query(AdminAuditLog)
            .filter(AdminAuditLog.action == "auth.ui_locale_changed")
            .one()
        )
        assert audit.actor_id == operator.id
        assert audit.target_id == operator.id
        assert "zh-CN" in str(audit.old_value_json)
        assert "en" in str(audit.new_value_json)

        german = client.patch(
            "/api/auth/preferences",
            headers=headers,
            json={"ui_locale": "de"},
        )
        assert german.status_code == 200
        german_session = client.get("/api/auth/me", headers=headers).json()
        assert german_session["ui_locale"] == "de"
        assert german_session["ui_locale_configured"] is True

        montenegrin = client.patch(
            "/api/auth/preferences",
            headers=headers,
            json={"ui_locale": "CNR"},
        )
        assert montenegrin.status_code == 200, montenegrin.text
        assert montenegrin.json() == {"ui_locale": "cnr"}
        montenegrin_session = client.get("/api/auth/me", headers=headers).json()
        assert montenegrin_session["ui_locale"] == "cnr"
        assert montenegrin_session["ui_locale_configured"] is True

        db_session.refresh(operator)
        assert _database_neutral_timestamp(operator.updated_at) == identity_version

        invalid = client.patch(
            "/api/auth/preferences",
            headers=headers,
            json={"ui_locale": "fr"},
        )
        assert invalid.status_code == 422
        assert client.get("/api/auth/me", headers=headers).json()["ui_locale"] == "cnr"
    finally:
        _close(db_session, engine)
