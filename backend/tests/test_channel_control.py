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
from app.api.channel_control import (  # noqa: E402
    cancel_onboarding_task,
    complete_onboarding_task,
    create_onboarding_task,
    get_onboarding_task,
    list_onboarding_tasks,
    start_onboarding_task,
    update_onboarding_task,
)
from app.enums import UserRole  # noqa: E402
from app.models import Market, User  # noqa: E402
from app.schemas_channel_control import (  # noqa: E402
    ChannelOnboardingTaskCompleteRequest,
    ChannelOnboardingTaskCreate,
    ChannelOnboardingTaskUpdate,
)
from app.services import channel_control_service  # noqa: E402


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


def _market(session) -> Market:
    row = Market(code="CH", name="Switzerland", country_code="CH", is_active=True)
    session.add(row)
    session.flush()
    return row


def _payload(**overrides) -> ChannelOnboardingTaskCreate:
    data = {
        "provider": "whatsapp",
        "target_slot": "zurich-primary",
        "desired_display_name": "Zurich WhatsApp",
        "desired_channel_account_binding": "wa-zurich-primary",
        "openclaw_account_id": "oc-wa-zurich",
    }
    data.update(overrides)
    return ChannelOnboardingTaskCreate(**data)


def test_agent_cannot_create_or_update_channel_control_task(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    agent = _user(db_session, UserRole.agent, "agent")
    task = create_onboarding_task(_payload(), db_session, admin)

    with pytest.raises(HTTPException) as create_exc:
        create_onboarding_task(_payload(target_slot="agent-slot"), db_session, agent)
    assert create_exc.value.status_code == 403

    with pytest.raises(HTTPException) as update_exc:
        update_onboarding_task(task.id, ChannelOnboardingTaskUpdate(target_slot="agent-update"), db_session, agent)
    assert update_exc.value.status_code == 403


def test_admin_can_create_and_list_tasks(db_session):
    admin = _user(db_session, UserRole.admin, "admin-channel")
    market = _market(db_session)

    task = create_onboarding_task(_payload(market_id=market.id), db_session, admin)
    assert task.provider == "whatsapp"
    assert task.status == "pending"
    assert task.requested_by == admin.id
    assert task.market_id == market.id

    listing = list_onboarding_tasks(provider="whatsapp", db=db_session, current_user=admin)
    assert listing.total == 1
    assert listing.tasks[0].id == task.id

    detail = get_onboarding_task(task.id, db_session, admin)
    assert detail.id == task.id


def test_invalid_provider_and_inactive_market_are_rejected(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    inactive = Market(code="XX", name="Inactive", country_code="XX", is_active=False)
    db_session.add(inactive)
    db_session.flush()

    with pytest.raises(HTTPException) as provider_exc:
        create_onboarding_task(_payload(provider="unknown"), db_session, admin)
    assert provider_exc.value.status_code == 400

    with pytest.raises(HTTPException) as market_exc:
        create_onboarding_task(_payload(market_id=inactive.id), db_session, admin)
    assert market_exc.value.status_code == 400


def test_update_task_fields_before_terminal_state(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    task = create_onboarding_task(_payload(), db_session, admin)

    updated = update_onboarding_task(
        task.id,
        ChannelOnboardingTaskUpdate(target_slot="bern-primary", desired_display_name="Bern WhatsApp"),
        db_session,
        admin,
    )
    assert updated.target_slot == "bern-primary"
    assert updated.desired_display_name == "Bern WhatsApp"


def test_status_flow_start_complete_and_terminal_immutable(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    task = create_onboarding_task(_payload(), db_session, admin)

    started = start_onboarding_task(task.id, db_session, admin)
    assert started.status == "in_progress"
    assert started.started_at is not None

    completed = complete_onboarding_task(
        task.id,
        ChannelOnboardingTaskCompleteRequest(openclaw_account_id="oc-final", desired_channel_account_binding="wa-final"),
        db_session,
        admin,
    )
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.openclaw_account_id == "oc-final"
    assert completed.desired_channel_account_binding == "wa-final"

    with pytest.raises(HTTPException) as update_exc:
        update_onboarding_task(task.id, ChannelOnboardingTaskUpdate(target_slot="too-late"), db_session, admin)
    assert update_exc.value.status_code == 400


def test_fail_then_complete_task(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    task = channel_control_service.create_task(db_session, _payload(), admin)

    failed = channel_control_service.fail_task(
        db_session,
        task,
        type("Payload", (), {"last_error": "pairing failed"})(),
    )
    assert failed.status == "failed"
    assert failed.last_error == "pairing failed"

    completed = complete_onboarding_task(
        task.id,
        ChannelOnboardingTaskCompleteRequest(openclaw_account_id="oc-recovered"),
        db_session,
        admin,
    )
    assert completed.status == "completed"
    assert completed.last_error is None


def test_cancel_task_is_terminal(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    task = create_onboarding_task(_payload(), db_session, admin)

    cancelled = cancel_onboarding_task(task.id, db_session, admin)
    assert cancelled.status == "cancelled"

    with pytest.raises(HTTPException) as start_exc:
        start_onboarding_task(task.id, db_session, admin)
    assert start_exc.value.status_code == 400


def test_invalid_status_filter_rejected(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    with pytest.raises(HTTPException) as exc:
        list_onboarding_tasks(status="running", db=db_session, current_user=admin)
    assert exc.value.status_code == 400


def test_empty_fail_error_fails_validation():
    from app.schemas_channel_control import ChannelOnboardingTaskFailRequest

    with pytest.raises(ValidationError):
        ChannelOnboardingTaskFailRequest(last_error="   ")
