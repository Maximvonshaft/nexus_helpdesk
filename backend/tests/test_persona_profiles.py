import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402
from app import models_control_plane  # noqa: F401,E402
from app.api.persona_profiles import (  # noqa: E402
    create_persona_profile,
    get_persona_profile,
    list_persona_profiles,
    publish_persona_profile,
    resolve_persona_preview,
    rollback_persona_profile,
    update_persona_profile,
)
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.schemas_control_plane import (  # noqa: E402
    PersonaProfileCreate,
    PersonaProfileUpdate,
    PersonaPublishRequest,
    PersonaResolvePreviewRequest,
    PersonaRollbackRequest,
)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _user(session, role: UserRole, username: str) -> User:
    row = User(
        username=username,
        display_name=username.title(),
        email=f"{username}@example.test",
        password_hash="not-a-real-password-hash",
        role=role,
        is_active=True,
    )
    session.add(row)
    session.flush()
    return row


def _create_payload(**overrides) -> PersonaProfileCreate:
    data = {
        "profile_key": "default.whatsapp.en",
        "name": "Default WhatsApp English",
        "description": "Default English WhatsApp support persona",
        "channel": "whatsapp",
        "language": "en",
        "is_active": True,
        "draft_summary": "Clear and concise",
        "draft_content_json": {"tone": "clear", "style": "concise"},
    }
    data.update(overrides)
    return PersonaProfileCreate(**data)


def test_agent_cannot_create_update_publish_or_rollback(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    agent = _user(db_session, UserRole.agent, "agent")
    profile = create_persona_profile(_create_payload(profile_key="protected.persona"), db_session, admin)

    with pytest.raises(HTTPException) as create_exc:
        create_persona_profile(_create_payload(profile_key="agent.create"), db_session, agent)
    assert create_exc.value.status_code == 403

    with pytest.raises(HTTPException) as update_exc:
        update_persona_profile(profile.id, PersonaProfileUpdate(name="Agent Update"), db_session, agent)
    assert update_exc.value.status_code == 403

    with pytest.raises(HTTPException) as publish_exc:
        publish_persona_profile(profile.id, PersonaPublishRequest(notes="try"), db_session, agent)
    assert publish_exc.value.status_code == 403

    publish_persona_profile(profile.id, PersonaPublishRequest(notes="admin publish"), db_session, admin)
    with pytest.raises(HTTPException) as rollback_exc:
        rollback_persona_profile(profile.id, PersonaRollbackRequest(version=1, notes="try"), db_session, agent)
    assert rollback_exc.value.status_code == 403


def test_admin_can_create_profile(db_session):
    admin = _user(db_session, UserRole.admin, "admin-persona")
    profile = create_persona_profile(_create_payload(profile_key="admin.persona"), db_session, admin)

    assert profile.profile_key == "admin.persona"
    assert profile.created_by == admin.id
    assert profile.updated_by == admin.id
    assert profile.published_version == 0


def test_duplicate_profile_key_returns_409(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    create_persona_profile(_create_payload(profile_key="duplicate.persona"), db_session, admin)

    with pytest.raises(HTTPException) as exc:
        create_persona_profile(_create_payload(profile_key="duplicate.persona"), db_session, admin)
    assert exc.value.status_code == 409


def test_invalid_profile_key_fails_validation():
    with pytest.raises(ValidationError):
        PersonaProfileCreate(profile_key="Bad Key", name="Invalid")


def test_list_and_get_profiles_work(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    create_persona_profile(_create_payload(profile_key="b.persona", name="B Persona"), db_session, admin)
    created = create_persona_profile(_create_payload(profile_key="a.persona", name="A Persona"), db_session, admin)

    listing = list_persona_profiles(db=db_session, current_user=admin)
    assert listing.total == 2
    assert [item.profile_key for item in listing.profiles] == ["a.persona", "b.persona"]

    detail = get_persona_profile(created.id, db_session, admin)
    assert detail.id == created.id
    assert detail.profile_key == "a.persona"
    assert detail.versions == []


def test_update_draft_works_without_touching_published_fields(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    profile = create_persona_profile(_create_payload(profile_key="updatable.persona"), db_session, admin)
    publish_persona_profile(profile.id, PersonaPublishRequest(notes="publish v1"), db_session, admin)

    updated = update_persona_profile(
        profile.id,
        PersonaProfileUpdate(
            name="Updated Persona",
            draft_summary="Updated draft",
            draft_content_json={"tone": "warm"},
        ),
        db_session,
        admin,
    )

    assert updated.name == "Updated Persona"
    assert updated.draft_summary == "Updated draft"
    assert updated.published_version == 1
    assert updated.published_summary == "Clear and concise"


def test_publish_empty_draft_returns_400(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    profile = create_persona_profile(
        _create_payload(profile_key="empty.draft", draft_summary=None, draft_content_json={}),
        db_session,
        admin,
    )

    with pytest.raises(HTTPException) as exc:
        publish_persona_profile(profile.id, PersonaPublishRequest(notes="empty"), db_session, admin)
    assert exc.value.status_code == 400


def test_publish_valid_draft_creates_and_increments_versions(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    profile = create_persona_profile(_create_payload(profile_key="publish.persona"), db_session, admin)

    v1 = publish_persona_profile(profile.id, PersonaPublishRequest(notes="v1"), db_session, admin)
    assert v1.version == 1
    assert v1.summary == "Clear and concise"

    update_persona_profile(
        profile.id,
        PersonaProfileUpdate(draft_summary="Second summary", draft_content_json={"tone": "formal"}),
        db_session,
        admin,
    )
    v2 = publish_persona_profile(profile.id, PersonaPublishRequest(notes="v2"), db_session, admin)
    assert v2.version == 2

    detail = get_persona_profile(profile.id, db_session, admin)
    assert detail.published_version == 2
    assert len(detail.versions) == 2


def test_rollback_to_previous_version_works(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    profile = create_persona_profile(_create_payload(profile_key="rollback.persona"), db_session, admin)
    publish_persona_profile(profile.id, PersonaPublishRequest(notes="v1"), db_session, admin)
    update_persona_profile(
        profile.id,
        PersonaProfileUpdate(draft_summary="Second summary", draft_content_json={"tone": "formal"}),
        db_session,
        admin,
    )
    publish_persona_profile(profile.id, PersonaPublishRequest(notes="v2"), db_session, admin)

    rollback = rollback_persona_profile(profile.id, PersonaRollbackRequest(version=1, notes="rollback"), db_session, admin)
    assert rollback.version == 3

    detail = get_persona_profile(profile.id, db_session, admin)
    assert detail.published_version == 3
    assert detail.published_summary == "Clear and concise"
    assert detail.published_content_json == {"tone": "clear", "style": "concise"}


def test_rollback_missing_version_returns_404(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    profile = create_persona_profile(_create_payload(profile_key="missing.rollback"), db_session, admin)

    with pytest.raises(HTTPException) as exc:
        rollback_persona_profile(profile.id, PersonaRollbackRequest(version=99, notes="missing"), db_session, admin)
    assert exc.value.status_code == 404


def test_resolve_preview_uses_deterministic_priority(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    global_profile = create_persona_profile(
        _create_payload(profile_key="global.default", channel=None, language=None, draft_summary="global"),
        db_session,
        admin,
    )
    channel_profile = create_persona_profile(
        _create_payload(profile_key="channel.default", channel="whatsapp", language=None, draft_summary="channel"),
        db_session,
        admin,
    )
    exact_profile = create_persona_profile(
        _create_payload(profile_key="exact.default", market_id=1, channel="whatsapp", language="en", draft_summary="exact"),
        db_session,
        admin,
    )
    for profile in (global_profile, channel_profile, exact_profile):
        publish_persona_profile(profile.id, PersonaPublishRequest(notes="publish"), db_session, admin)

    resolved = resolve_persona_preview(
        PersonaResolvePreviewRequest(market_id=1, channel="whatsapp", language="en"),
        db_session,
        admin,
    )
    assert resolved.profile is not None
    assert resolved.profile.profile_key == "exact.default"
    assert resolved.match_rank == 1


def test_inactive_and_unpublished_profiles_are_not_resolved(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    inactive = create_persona_profile(
        _create_payload(profile_key="inactive.persona", is_active=False, channel="whatsapp"),
        db_session,
        admin,
    )
    unpublished = create_persona_profile(
        _create_payload(profile_key="unpublished.persona", channel="whatsapp"),
        db_session,
        admin,
    )
    publish_persona_profile(inactive.id, PersonaPublishRequest(notes="inactive publish"), db_session, admin)

    resolved = resolve_persona_preview(
        PersonaResolvePreviewRequest(channel="whatsapp"),
        db_session,
        admin,
    )
    assert resolved.profile is None
    assert unpublished.published_version == 0
