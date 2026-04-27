import sys
from datetime import timedelta
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
from app.api.knowledge_items import (  # noqa: E402
    create_knowledge_item,
    get_knowledge_item,
    list_knowledge_items,
    publish_knowledge_item,
    rollback_knowledge_item,
    search_published_knowledge_items,
    update_knowledge_item,
)
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.schemas_control_plane import (  # noqa: E402
    KnowledgeItemCreate,
    KnowledgeItemUpdate,
    KnowledgePublishRequest,
    KnowledgeRollbackRequest,
    KnowledgeSearchPublishedRequest,
)
from app.utils.time import utc_now  # noqa: E402


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


def _create_payload(**overrides) -> KnowledgeItemCreate:
    data = {
        "item_key": "delivery.faq",
        "title": "Delivery FAQ",
        "summary": "Common delivery answers",
        "status": "draft",
        "source_type": "text",
        "channel": "whatsapp",
        "audience_scope": "customer",
        "priority": 100,
        "draft_body": "Customers can change delivery address before dispatch.",
        "draft_normalized_text": "change delivery address before dispatch",
    }
    data.update(overrides)
    return KnowledgeItemCreate(**data)


def test_agent_cannot_create_update_publish_or_rollback(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    agent = _user(db_session, UserRole.agent, "agent")
    item = create_knowledge_item(_create_payload(item_key="protected.knowledge"), db_session, admin)

    with pytest.raises(HTTPException) as create_exc:
        create_knowledge_item(_create_payload(item_key="agent.create"), db_session, agent)
    assert create_exc.value.status_code == 403

    with pytest.raises(HTTPException) as update_exc:
        update_knowledge_item(item.id, KnowledgeItemUpdate(title="Agent Update"), db_session, agent)
    assert update_exc.value.status_code == 403

    with pytest.raises(HTTPException) as publish_exc:
        publish_knowledge_item(item.id, KnowledgePublishRequest(notes="try"), db_session, agent)
    assert publish_exc.value.status_code == 403

    publish_knowledge_item(item.id, KnowledgePublishRequest(notes="admin publish"), db_session, admin)
    with pytest.raises(HTTPException) as rollback_exc:
        rollback_knowledge_item(item.id, KnowledgeRollbackRequest(version=1, notes="try"), db_session, agent)
    assert rollback_exc.value.status_code == 403


def test_admin_can_create_knowledge_item(db_session):
    admin = _user(db_session, UserRole.admin, "admin-knowledge")
    item = create_knowledge_item(_create_payload(item_key="admin.knowledge"), db_session, admin)

    assert item.item_key == "admin.knowledge"
    assert item.created_by == admin.id
    assert item.updated_by == admin.id
    assert item.published_version == 0


def test_duplicate_item_key_returns_409(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    create_knowledge_item(_create_payload(item_key="duplicate.knowledge"), db_session, admin)

    with pytest.raises(HTTPException) as exc:
        create_knowledge_item(_create_payload(item_key="duplicate.knowledge"), db_session, admin)
    assert exc.value.status_code == 409


def test_invalid_item_key_fails_validation():
    with pytest.raises(ValidationError):
        KnowledgeItemCreate(item_key="Bad Key", title="Invalid")


def test_unsupported_status_or_source_type_returns_400(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    with pytest.raises(HTTPException) as status_exc:
        create_knowledge_item(_create_payload(item_key="bad.status", status="enabled"), db_session, admin)
    assert status_exc.value.status_code == 400

    with pytest.raises(HTTPException) as type_exc:
        create_knowledge_item(_create_payload(item_key="bad.source", source_type="binary"), db_session, admin)
    assert type_exc.value.status_code == 400


def test_list_and_get_items_work(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    create_knowledge_item(_create_payload(item_key="b.knowledge", title="B Knowledge"), db_session, admin)
    created = create_knowledge_item(_create_payload(item_key="a.knowledge", title="A Knowledge", priority=10), db_session, admin)

    listing = list_knowledge_items(db=db_session, current_user=admin)
    assert listing.total == 2
    assert [item.item_key for item in listing.items] == ["a.knowledge", "b.knowledge"]

    detail = get_knowledge_item(created.id, db_session, admin)
    assert detail.id == created.id
    assert detail.item_key == "a.knowledge"
    assert detail.versions == []


def test_update_draft_works_without_touching_published_fields(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    item = create_knowledge_item(_create_payload(item_key="updatable.knowledge"), db_session, admin)
    publish_knowledge_item(item.id, KnowledgePublishRequest(notes="publish v1"), db_session, admin)

    updated = update_knowledge_item(
        item.id,
        KnowledgeItemUpdate(title="Updated Knowledge", draft_body="Updated draft body"),
        db_session,
        admin,
    )

    assert updated.title == "Updated Knowledge"
    assert updated.draft_body == "Updated draft body"
    assert updated.draft_normalized_text == "Updated draft body"
    assert updated.published_version == 1
    assert updated.published_body == "Customers can change delivery address before dispatch."


def test_publish_empty_draft_returns_400(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    item = create_knowledge_item(
        _create_payload(item_key="empty.draft", draft_body=None, draft_normalized_text=None),
        db_session,
        admin,
    )

    with pytest.raises(HTTPException) as exc:
        publish_knowledge_item(item.id, KnowledgePublishRequest(notes="empty"), db_session, admin)
    assert exc.value.status_code == 400


def test_publish_valid_draft_creates_and_increments_versions(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    item = create_knowledge_item(_create_payload(item_key="publish.knowledge"), db_session, admin)

    v1 = publish_knowledge_item(item.id, KnowledgePublishRequest(notes="v1"), db_session, admin)
    assert v1.version == 1
    assert v1.summary == "Common delivery answers"
    assert item.status == "active"

    update_knowledge_item(
        item.id,
        KnowledgeItemUpdate(draft_body="Second body", draft_normalized_text="second searchable body"),
        db_session,
        admin,
    )
    v2 = publish_knowledge_item(item.id, KnowledgePublishRequest(notes="v2"), db_session, admin)
    assert v2.version == 2

    detail = get_knowledge_item(item.id, db_session, admin)
    assert detail.published_version == 2
    assert len(detail.versions) == 2


def test_rollback_to_previous_version_works(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    item = create_knowledge_item(_create_payload(item_key="rollback.knowledge"), db_session, admin)
    publish_knowledge_item(item.id, KnowledgePublishRequest(notes="v1"), db_session, admin)
    update_knowledge_item(
        item.id,
        KnowledgeItemUpdate(draft_body="Second body", draft_normalized_text="second searchable body"),
        db_session,
        admin,
    )
    publish_knowledge_item(item.id, KnowledgePublishRequest(notes="v2"), db_session, admin)

    rollback = rollback_knowledge_item(item.id, KnowledgeRollbackRequest(version=1, notes="rollback"), db_session, admin)
    assert rollback.version == 3

    detail = get_knowledge_item(item.id, db_session, admin)
    assert detail.published_version == 3
    assert detail.published_body == "Customers can change delivery address before dispatch."
    assert detail.published_normalized_text == "change delivery address before dispatch"


def test_rollback_missing_version_returns_404(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    item = create_knowledge_item(_create_payload(item_key="missing.rollback"), db_session, admin)

    with pytest.raises(HTTPException) as exc:
        rollback_knowledge_item(item.id, KnowledgeRollbackRequest(version=99, notes="missing"), db_session, admin)
    assert exc.value.status_code == 404


def test_search_published_returns_only_active_published_items(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    agent = _user(db_session, UserRole.agent, "agent")
    active = create_knowledge_item(
        _create_payload(item_key="active.customer", title="Address Change", market_id=1, channel="whatsapp", priority=10),
        db_session,
        admin,
    )
    draft = create_knowledge_item(_create_payload(item_key="draft.customer", channel="whatsapp"), db_session, admin)
    archived = create_knowledge_item(_create_payload(item_key="archived.customer", status="archived", channel="whatsapp"), db_session, admin)
    expired = create_knowledge_item(
        _create_payload(
            item_key="expired.customer",
            channel="whatsapp",
            starts_at=utc_now() - timedelta(days=3),
            ends_at=utc_now() - timedelta(days=1),
        ),
        db_session,
        admin,
    )
    for item in (active, archived, expired):
        publish_knowledge_item(item.id, KnowledgePublishRequest(notes="publish"), db_session, admin)

    result = search_published_knowledge_items(
        KnowledgeSearchPublishedRequest(q="address", market_id=1, channel="whatsapp", audience_scope="customer"),
        db_session,
        agent,
    )
    assert result.total == 1
    assert result.items[0].item_key == "active.customer"
    assert draft.published_version == 0


def test_search_published_includes_global_fallback(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    global_item = create_knowledge_item(
        _create_payload(item_key="global.customer", market_id=None, channel=None, priority=20),
        db_session,
        admin,
    )
    market_item = create_knowledge_item(
        _create_payload(item_key="market.customer", market_id=2, channel="email", priority=10),
        db_session,
        admin,
    )
    publish_knowledge_item(global_item.id, KnowledgePublishRequest(notes="global"), db_session, admin)
    publish_knowledge_item(market_item.id, KnowledgePublishRequest(notes="market"), db_session, admin)

    result = search_published_knowledge_items(
        KnowledgeSearchPublishedRequest(market_id=2, channel="email", audience_scope="customer"),
        db_session,
        admin,
    )
    assert [item.item_key for item in result.items] == ["market.customer", "global.customer"]
