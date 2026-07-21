from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import models_control_plane as _models_control_plane  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _user(db_session, *, role: UserRole, suffix: str = "") -> User:
    row = User(
        username=f"{role.value}_persona_review{suffix}",
        display_name=f"{role.value.title()} Persona Review",
        email=f"{role.value}.persona_review{suffix}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _profile_payload(profile_key: str = "persona.review.webchat"):
    return {
        "profile_key": profile_key,
        "name": "Persona Review WebChat",
        "description": "Review-gated WebChat Persona",
        "market_id": 1,
        "channel": "webchat",
        "language": "en",
        "draft_summary": "Review-gated summary",
        "draft_content_json": {
            "schema_version": "nexus.persona.v1",
            "brand_name": "Nexus Express",
            "assistant_name": "Nora",
            "identity_statement": "Nora helps customers with delivery support.",
            "handoff_boundary": "Escalate legal and cancellation requests.",
            "guardrails": ["Do not invent parcel status."],
        },
    }


def _setup_db(tmp_path):
    db_file = tmp_path / "persona_review.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    author = _user(db_session, role=UserRole.admin, suffix="_author")
    reviewer = _user(db_session, role=UserRole.manager, suffix="_reviewer")
    agent = _user(db_session, role=UserRole.agent, suffix="_agent")
    db_session.commit()
    return engine, db_session, author, reviewer, agent


def test_persona_review_submit_approve_release_window_publish_contract(tmp_path):
    engine, db_session, author, reviewer, _agent = _setup_db(tmp_path)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        profile_response = client.post(
            "/api/persona-profiles",
            headers=_headers(author),
            json=_profile_payload(),
        )
        assert profile_response.status_code == 200, profile_response.text
        profile = profile_response.json()

        submit_response = client.post(
            f"/api/persona-profiles/{profile['id']}/submit-review",
            headers=_headers(author),
            json={
                "notes": "ready for release",
                "release_window_start": (utc_now() - timedelta(minutes=5)).isoformat(),
                "release_window_end": (utc_now() + timedelta(days=1)).isoformat(),
            },
        )
        duplicate_response = client.post(
            f"/api/persona-profiles/{profile['id']}/submit-review",
            headers=_headers(author),
            json={"notes": "duplicate"},
        )
        list_response = client.get(
            "/api/persona-profiles/reviews?status=pending",
            headers=_headers(author),
        )
        assert submit_response.status_code == 200, submit_response.text
        review = submit_response.json()
        approve_response = client.post(
            f"/api/persona-profiles/reviews/{review['id']}/approve",
            headers=_headers(reviewer),
            json={"decision_note": "approved for controlled release"},
        )
        publish_response = client.post(
            f"/api/persona-profiles/reviews/{review['id']}/publish",
            headers=_headers(reviewer),
            json={"notes": "publish approved review"},
        )
        detail_response = client.get(
            f"/api/persona-profiles/{profile['id']}",
            headers=_headers(author),
        )
        builder_response = client.get(
            "/api/lite/persona-builder",
            headers=_headers(author),
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert review["profile_id"] == profile["id"]
    assert review["review_version"] == 1
    assert review["status"] == "pending"
    assert review["snapshot_json"]["summary"] == "Review-gated summary"
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"] == "persona_review_already_pending"
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["total"] == 1
    assert approve_response.status_code == 200, approve_response.text
    assert approve_response.json()["status"] == "approved"
    assert approve_response.json()["reviewed_by"] == reviewer.id
    assert publish_response.status_code == 200, publish_response.text
    assert publish_response.json()["version"] == 1
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["published_version"] == 1
    assert builder_response.status_code == 200, builder_response.text
    builder = builder_response.json()
    blocks = {item["key"]: item for item in builder["template_blocks"]}
    lifecycle = {item["key"]: item for item in builder["release_lifecycle"]}
    assert blocks["approval"]["status"] == "implemented"
    assert lifecycle["approval"]["status"] == "implemented"
    assert builder["facts"]["submit_review_endpoint"] == "implemented"
    assert builder["facts"]["approval_endpoint"] == "implemented"
    assert builder["facts"]["release_window_command"] == "implemented"


def test_persona_review_publish_respects_future_release_window(tmp_path):
    engine, db_session, author, reviewer, _agent = _setup_db(tmp_path)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        profile_response = client.post(
            "/api/persona-profiles",
            headers=_headers(author),
            json=_profile_payload("persona.review.future"),
        )
        assert profile_response.status_code == 200, profile_response.text
        profile = profile_response.json()
        submit_response = client.post(
            f"/api/persona-profiles/{profile['id']}/submit-review",
            headers=_headers(author),
            json={
                "release_window_start": (
                    utc_now() + timedelta(days=1)
                ).isoformat()
            },
        )
        assert submit_response.status_code == 200, submit_response.text
        review = submit_response.json()
        approve_response = client.post(
            f"/api/persona-profiles/reviews/{review['id']}/approve",
            headers=_headers(reviewer),
            json={"decision_note": "approved for tomorrow"},
        )
        publish_response = client.post(
            f"/api/persona-profiles/reviews/{review['id']}/publish",
            headers=_headers(reviewer),
            json={"notes": "too early"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert approve_response.status_code == 200, approve_response.text
    assert publish_response.status_code == 409
    assert publish_response.json()["detail"] == "persona_release_window_not_open"


def test_persona_review_requires_ai_config_manage(tmp_path):
    engine, db_session, author, _reviewer, agent = _setup_db(tmp_path)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        profile_response = client.post(
            "/api/persona-profiles",
            headers=_headers(author),
            json=_profile_payload("persona.review.forbidden"),
        )
        assert profile_response.status_code == 200, profile_response.text
        profile = profile_response.json()
        response = client.post(
            f"/api/persona-profiles/{profile['id']}/submit-review",
            headers=_headers(agent),
            json={"notes": "agent try"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to manage AI config"
