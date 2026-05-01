import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402
from app import models_control_plane  # noqa: F401,E402
from app.models import User, UserRole  # noqa: E402
from app.models_control_plane import (  # noqa: E402
    ChannelOnboardingTask,
    KnowledgeItem,
    KnowledgeItemVersion,
    PersonaProfile,
    PersonaProfileVersion,
)

CONTROL_PLANE_TABLES = {
    "persona_profiles",
    "persona_profile_versions",
    "knowledge_items",
    "knowledge_item_versions",
    "channel_onboarding_tasks",
}


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _flush_raises_integrity_error(session):
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def _seed_user(session, username="admin") -> User:
    user = User(
        username=username,
        display_name="Admin User",
        email=f"{username}@example.test",
        password_hash="not-a-real-password-hash",
        role=UserRole.admin,
    )
    session.add(user)
    session.flush()
    return user


def test_control_plane_models_are_registered_in_base_metadata():
    assert CONTROL_PLANE_TABLES.issubset(set(Base.metadata.tables.keys()))


def test_basic_persona_profile_insert_query_and_nullable_fks(db_session):
    profile = PersonaProfile(
        profile_key="default-whatsapp-en",
        name="Default WhatsApp English",
        market_id=None,
        created_by=None,
        draft_content_json={"tone": "clear"},
    )
    db_session.add(profile)
    db_session.flush()

    found = db_session.query(PersonaProfile).filter_by(profile_key="default-whatsapp-en").one()
    assert found.id == profile.id
    assert found.market_id is None
    assert found.created_by is None
    assert found.published_version == 0


def test_persona_profile_key_is_unique(db_session):
    db_session.add(PersonaProfile(profile_key="duplicate", name="First"))
    db_session.flush()
    db_session.add(PersonaProfile(profile_key="duplicate", name="Second"))

    _flush_raises_integrity_error(db_session)


def test_persona_profile_version_uniqueness(db_session):
    user = _seed_user(db_session)
    profile = PersonaProfile(profile_key="versioned-persona", name="Versioned Persona", created_by=user.id)
    db_session.add(profile)
    db_session.flush()

    db_session.add(
        PersonaProfileVersion(
            profile_id=profile.id,
            version=1,
            snapshot_json={"tone": "direct"},
            published_by=user.id,
        )
    )
    db_session.flush()
    db_session.add(
        PersonaProfileVersion(
            profile_id=profile.id,
            version=1,
            snapshot_json={"tone": "formal"},
            published_by=user.id,
        )
    )

    _flush_raises_integrity_error(db_session)


def test_basic_knowledge_item_insert_query_and_nullable_fks(db_session):
    item = KnowledgeItem(
        item_key="delivery-faq",
        title="Delivery FAQ",
        market_id=None,
        created_by=None,
        draft_body="A short operational answer.",
    )
    db_session.add(item)
    db_session.flush()

    found = db_session.query(KnowledgeItem).filter_by(item_key="delivery-faq").one()
    assert found.id == item.id
    assert found.market_id is None
    assert found.created_by is None
    assert found.status == "draft"
    assert found.source_type == "text"
    assert found.audience_scope == "customer"
    assert found.priority == 100


def test_knowledge_item_key_is_unique(db_session):
    db_session.add(KnowledgeItem(item_key="duplicate", title="First"))
    db_session.flush()
    db_session.add(KnowledgeItem(item_key="duplicate", title="Second"))

    _flush_raises_integrity_error(db_session)


def test_knowledge_item_version_uniqueness(db_session):
    user = _seed_user(db_session)
    item = KnowledgeItem(item_key="versioned-knowledge", title="Versioned Knowledge", created_by=user.id)
    db_session.add(item)
    db_session.flush()

    db_session.add(
        KnowledgeItemVersion(
            item_id=item.id,
            version=1,
            snapshot_json={"body": "first"},
            published_by=user.id,
        )
    )
    db_session.flush()
    db_session.add(
        KnowledgeItemVersion(
            item_id=item.id,
            version=1,
            snapshot_json={"body": "second"},
            published_by=user.id,
        )
    )

    _flush_raises_integrity_error(db_session)


def test_basic_channel_onboarding_task_insert_query_and_nullable_fks(db_session):
    task = ChannelOnboardingTask(
        provider="whatsapp",
        status="pending",
        requested_by=None,
        market_id=None,
        target_slot="zurich-primary",
        desired_display_name="Zurich WhatsApp",
    )
    db_session.add(task)
    db_session.flush()

    found = db_session.query(ChannelOnboardingTask).filter_by(provider="whatsapp").one()
    assert found.id == task.id
    assert found.status == "pending"
    assert found.requested_by is None
    assert found.market_id is None
    assert found.target_slot == "zurich-primary"
