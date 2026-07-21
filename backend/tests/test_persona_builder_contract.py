from __future__ import annotations

import os
import sys
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
from app.schemas_control_plane import PersonaProfileCreate, PersonaProfileUpdate  # noqa: E402
from app.services import persona_service  # noqa: E402
from app.services.ai_runtime_context import build_agent_context  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _user(db_session, *, role: UserRole, suffix: str = "") -> User:
    row = User(
        username=f"{role.value}_persona_builder{suffix}",
        display_name=f"{role.value.title()} Persona Builder",
        email=f"{role.value}.persona_builder{suffix}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _identity_content(**overrides):
    content = {
        "schema_version": "nexus.persona.v1",
        "brand_name": "Nexus Express",
        "assistant_name": "Nora",
        "role_label": "Customer support assistant",
        "identity_statement": "Nora helps customers with delivery support on behalf of Nexus Express.",
        "identity_answer_rule": "Answer identity questions with the approved brand and support scope.",
        "capabilities": ["tracking explanation", "delivery appointment guidance"],
        "disallowed_identity_claims": ["Do not claim to be a human agent."],
        "handoff_boundary": "Escalate to a human when the customer requests cancellation or legal review.",
        "guardrails": ["Do not invent live parcel status.", "Do not expose internal tooling."],
        "tone": "calm and concise",
    }
    content.update(overrides)
    return content


def _persona_payload(**overrides) -> PersonaProfileCreate:
    data = {
        "profile_key": "persona.builder.webchat.en",
        "name": "WebChat English Persona",
        "description": "Customer-facing WebChat persona",
        "market_id": 1,
        "channel": "webchat",
        "language": "en",
        "is_active": True,
        "draft_summary": "Nexus webchat persona v1",
        "draft_content_json": _identity_content(),
    }
    data.update(overrides)
    return PersonaProfileCreate(**data)


def _seed_persona_builder(db_session):
    admin = _user(db_session, role=UserRole.admin)
    agent = _user(db_session, role=UserRole.agent, suffix="_agent")
    exact = persona_service.create_profile(db_session, _persona_payload(), admin)
    persona_service.publish_profile(
        db_session,
        exact,
        admin,
        notes="approved exact fixture",
    )
    persona_service.update_profile(
        db_session,
        exact,
        PersonaProfileUpdate(
            draft_summary="Nexus webchat persona v2 pending",
            draft_content_json=_identity_content(
                tone="more direct",
                guardrails=["Ask one clarifying question before handoff."],
            ),
        ),
        admin,
    )
    global_fallback = persona_service.create_profile(
        db_session,
        _persona_payload(
            profile_key="persona.builder.global",
            name="Global fallback persona",
            description="Global fallback for any channel",
            market_id=None,
            channel=None,
            language=None,
            draft_summary="Global fallback persona",
            draft_content_json=_identity_content(
                assistant_name="Nexus Assistant"
            ),
        ),
        admin,
    )
    persona_service.publish_profile(
        db_session,
        global_fallback,
        admin,
        notes="approved global fixture",
    )
    email_draft = persona_service.create_profile(
        db_session,
        _persona_payload(
            profile_key="persona.builder.email.draft",
            name="Email draft persona",
            market_id=None,
            channel="email",
            language="en",
            draft_summary="Email draft not yet published",
            draft_content_json=_identity_content(
                assistant_name="Nexus Email Assistant"
            ),
        ),
        admin,
    )
    db_session.flush()
    return admin, agent, exact, global_fallback, email_draft


def test_persona_builder_separates_authoring_preview_from_runtime_deployment(tmp_path):
    db_file = tmp_path / "persona_builder.db"
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
    admin, _agent, exact, global_fallback, email_draft = _seed_persona_builder(
        db_session
    )
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/lite/persona-builder",
            headers=_headers(admin),
        )
        preview = client.post(
            "/api/persona-profiles/resolve-preview",
            headers=_headers(admin),
            json={"market_id": 1, "channel": "webchat", "language": "en"},
        )
        runtime_evidence = client.post(
            "/api/persona-profiles/runtime-evidence",
            headers=_headers(admin),
            json={
                "tenant_key": "default",
                "body": "Who are you and can you help with delivery appointments?",
                "market_id": 1,
                "channel": "webchat",
                "language": "en",
                "audience_scope": "customer",
                "expected_profile_key": exact.profile_key,
            },
        )
        runtime_context = build_agent_context(
            db_session,
            tenant_key="default",
            channel_key="webchat",
            body="Who are you and can you help with delivery appointments?",
            market_id=1,
            language="en",
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 200, response.text
    payload = response.json()
    kpis = {item["key"]: item for item in payload["kpis"]}
    profiles = {item["profile_key"]: item for item in payload["profiles"]}
    blocks = {item["key"]: item for item in payload["template_blocks"]}
    lifecycle = {item["key"]: item for item in payload["release_lifecycle"]}

    assert payload["role"] == "admin"
    assert "ai_config.manage" in payload["capabilities"]
    assert kpis["total_profiles"]["value"] == 3
    assert kpis["published_profiles"]["value"] == 2
    assert kpis["needs_publish"]["value"] >= 2
    assert profiles[exact.profile_key]["published_ready"] is True
    assert profiles[exact.profile_key]["needs_publish"] is True
    assert profiles[exact.profile_key]["identity_ready"] is True
    assert profiles[exact.profile_key]["boundary_ready"] is True
    assert profiles[exact.profile_key]["guardrail_count"] >= 3
    assert profiles[global_fallback.profile_key]["scope_label"] == (
        "market:global / channel:global / lang:global"
    )
    assert profiles[email_draft.profile_key]["published_ready"] is False
    assert blocks["persona-list"]["status"] == "implemented"
    assert blocks["resolve-preview"]["backend_contract"] == (
        "POST /api/persona-profiles/resolve-preview"
    )
    assert blocks["approval"]["status"] == "implemented"
    assert blocks["runtime-evidence"]["status"] == "implemented"
    assert lifecycle["approval"]["status"] == "implemented"
    assert lifecycle["runtime-evidence"]["status"] == "implemented"
    assert lifecycle["published"]["count"] == 2
    assert payload["approval_queue"] == []
    assert payload["facts"]["submit_review_endpoint"] == "implemented"
    assert payload["facts"]["approval_endpoint"] == "implemented"
    assert payload["facts"]["release_window_command"] == "implemented"
    assert payload["facts"]["dedicated_runtime_evidence_endpoint"] == "implemented"
    assert any(
        item["matched_profile_key"] == exact.profile_key
        and item["match_rank"] == 0
        for item in payload["simulation_scenarios"]
    )

    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["profile"]["profile_key"] == exact.profile_key
    assert preview_payload["match_rank"] == 0

    assert runtime_evidence.status_code == 200, runtime_evidence.text
    runtime_payload = runtime_evidence.json()
    assert runtime_payload["matched_profile_key"] is None
    assert runtime_payload["match_rank"] is None
    assert runtime_payload["expected_profile_key"] == exact.profile_key
    assert runtime_payload["matched_expected"] is False
    assert runtime_payload["runtime_context"]["context_version"] == (
        "nexus.agent_context.v2"
    )
    assert runtime_payload["persona_context"] is None
    assert runtime_payload["evidence"]["runtime_contract"] == (
        "AgentDeployment->AgentRelease->build_agent_context"
    )
    assert runtime_payload["evidence"]["release_error"] == (
        "agent_deployment_unavailable"
    )
    assert runtime_payload["evidence"]["identity_ready"] is False

    assert runtime_context["persona_context"] is None
    assert runtime_context["agent_release_snapshot"] is None
    assert runtime_context["agent_release_error"] == (
        "agent_deployment_unavailable"
    )


def test_persona_builder_requires_ai_config_capability(tmp_path):
    db_file = tmp_path / "persona_builder_forbidden.db"
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
    _admin, agent, _exact, _global_fallback, _email_draft = (
        _seed_persona_builder(db_session)
    )
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/lite/persona-builder",
            headers=_headers(agent),
        )
        runtime_evidence = client.post(
            "/api/persona-profiles/runtime-evidence",
            headers=_headers(agent),
            json={"body": "Who are you?", "channel": "webchat"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "persona_builder_requires_ai_config_capability"
    )
    assert runtime_evidence.status_code == 403
