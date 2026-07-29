from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-r5-handoff.db",
)

from app.db import Base
from app.model_registry import register_all_models
from app.models import Tenant
from app.models_agent_routing import ConversationControl
from app.operator_models import (
    HANDOFF_PROJECTION_PRIORITY,
    HANDOFF_PROJECTION_SCHEMA,
    HANDOFF_PROJECTION_SOURCE,
    OperatorTask,
)
from app.services.agent_routing_service import request_handoff
from app.services.operator_queue import project_webchat_handoff_tasks
from app.webchat_models import WebchatConversation, WebchatHandoffRequest

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-838-r5-handoff.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _conversation(db, *, tenant_key: str, suffix: str) -> WebchatConversation:
    conversation = WebchatConversation(
        public_id=f"r5-handoff-{suffix}",
        visitor_token_hash=f"hash-{suffix}",
        tenant_key=tenant_key,
        channel_key="webchat",
        visitor_name=f"Customer {suffix}",
        origin="test",
        status="open",
    )
    db.add(conversation)
    db.flush()
    db.add(
        ConversationControl(
            conversation_id=conversation.id,
            customer_id=None,
            tenant_key=tenant_key,
            country_code="ME",
            channel_key="webchat",
        )
    )
    db.flush()
    return conversation


def _assert_canonical(task: OperatorTask, request: WebchatHandoffRequest) -> None:
    assert task.source_type == HANDOFF_PROJECTION_SOURCE
    assert task.source_id == str(request.id)
    assert task.source_version == request.lock_version
    assert task.projection_schema == HANDOFF_PROJECTION_SCHEMA
    assert task.priority == HANDOFF_PROJECTION_PRIORITY
    assert task.webchat_conversation_id == request.conversation_id


def test_realtime_handoff_and_rebuild_share_one_source_identity(db_session):
    tenant = Tenant(
        tenant_key="r5-handoff",
        display_name="R5 Handoff",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    conversation = _conversation(
        db_session,
        tenant_key=tenant.tenant_key,
        suffix="realtime",
    )

    request = request_handoff(
        db_session,
        conversation=conversation,
        source="ai_auto",
        trigger_type="handoff_required",
        reason_code="customer_requested_human",
    )
    task = db_session.query(OperatorTask).one()
    _assert_canonical(task, request)
    assert task.status == "pending"

    db_session.delete(task)
    db_session.flush()
    first = project_webchat_handoff_tasks(
        db_session,
        tenant_id=tenant.id,
    )
    assert first.created == 1
    rebuilt = db_session.query(OperatorTask).one()
    _assert_canonical(rebuilt, request)

    second = project_webchat_handoff_tasks(
        db_session,
        tenant_id=tenant.id,
    )
    assert second.created == 0
    assert db_session.query(OperatorTask).count() == 1

    request.status = "accepted"
    request.lock_version += 1
    db_session.flush()
    accepted = project_webchat_handoff_tasks(
        db_session,
        tenant_id=tenant.id,
    )
    assert accepted.skipped_existing == 1
    db_session.refresh(rebuilt)
    _assert_canonical(rebuilt, request)
    assert rebuilt.status == "assigned"

    request.status = "closed"
    request.lock_version += 1
    db_session.flush()
    closed = project_webchat_handoff_tasks(
        db_session,
        tenant_id=tenant.id,
    )
    assert closed.retired == 1
    db_session.refresh(rebuilt)
    assert rebuilt.source_type == HANDOFF_PROJECTION_SOURCE
    assert rebuilt.source_id == str(request.id)
    assert rebuilt.status == "resolved"


def test_legacy_realtime_identity_is_rewritten_before_persistence(db_session):
    tenant = Tenant(
        tenant_key="r5-legacy",
        display_name="R5 Legacy",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    conversation = _conversation(
        db_session,
        tenant_key=tenant.tenant_key,
        suffix="legacy",
    )
    request = WebchatHandoffRequest(
        conversation_id=conversation.id,
        source="ai_auto",
        trigger_type="handoff_required",
        status="requested",
        reason_code="legacy_realtime_write",
    )
    db_session.add(request)
    db_session.flush()

    legacy = OperatorTask(
        tenant_id=tenant.id,
        source_type="webchat",
        source_id=str(conversation.id),
        projection_schema="nexus.operator-task-projection.v1",
        webchat_conversation_id=conversation.id,
        task_type="handoff",
        status="pending",
        priority=100,
    )
    db_session.add(legacy)
    db_session.flush()

    _assert_canonical(legacy, request)
    assert legacy.reason_code == "legacy_realtime_write"
