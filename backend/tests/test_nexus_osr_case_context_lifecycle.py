from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models as _models  # noqa: F401
from app import webchat_models as _webchat_models  # noqa: F401
from app import models_osr as _models_osr  # noqa: F401
from app.models_osr import CaseContextRecord
from app.services.nexus_osr.case_context import CaseContext, CaseContextStatus
from app.services.nexus_osr.persistence import (
    close_case_context,
    expire_case_context,
    load_case_context,
    save_case_context,
)
from app.utils.time import utc_now


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'case-context.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _context(*, conversation_id=None, ticket_id=None, status=CaseContextStatus.ACTIVE):
    return CaseContext(
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        channel="webchat",
        country_code="ME",
        issue_type="delivery_delay",
        status=status,
    )


def test_exact_nullable_identity_combinations_and_unscoped_load_rejected(db):
    conversation_only = save_case_context(db, _context(conversation_id=101), tenant_id="tenant-a")
    ticket_only = save_case_context(db, _context(ticket_id=201), tenant_id="tenant-a")
    both = save_case_context(db, _context(conversation_id=101, ticket_id=201), tenant_id="tenant-a")
    unscoped = save_case_context(db, _context(), tenant_id="tenant-a")

    assert len({conversation_only.id, ticket_only.id, both.id, unscoped.id}) == 4
    assert unscoped.is_active is False
    assert load_case_context(db, conversation_id=101, tenant_id="tenant-a").ticket_id is None
    assert load_case_context(db, ticket_id=201, tenant_id="tenant-a").conversation_id is None
    assert load_case_context(db, conversation_id=101, ticket_id=201, tenant_id="tenant-a").ticket_id == 201
    assert load_case_context(db, ticket_id=201).conversation_id is None
    with pytest.raises(ValueError, match="case_context_identity_required"):
        load_case_context(db, tenant_id="tenant-a")


def test_single_selector_falls_back_to_one_unique_two_key_row(db):
    save_case_context(db, _context(conversation_id=111, ticket_id=211), tenant_id="tenant-a")

    by_conversation = load_case_context(db, conversation_id=111, tenant_id="tenant-a")
    by_ticket = load_case_context(db, ticket_id=211, tenant_id="tenant-a")

    assert by_conversation is not None and by_conversation.ticket_id == 211
    assert by_ticket is not None and by_ticket.conversation_id == 111


def test_single_selector_fallback_fails_closed_when_multiple_candidates_exist(db):
    save_case_context(db, _context(conversation_id=121, ticket_id=221), tenant_id="tenant-a")
    save_case_context(db, _context(conversation_id=122, ticket_id=221), tenant_id="tenant-a")

    with pytest.raises(ValueError, match="case_context_identity_ambiguous"):
        load_case_context(db, ticket_id=221, tenant_id="tenant-a")


def test_ticket_only_tenant_inference_fails_closed_when_ambiguous(db):
    save_case_context(db, _context(ticket_id=301), tenant_id="tenant-a")
    save_case_context(db, _context(ticket_id=301), tenant_id="tenant-b")
    with pytest.raises(ValueError, match="case_context_tenant_ambiguous"):
        load_case_context(db, ticket_id=301)


def test_tenant_scope_is_part_of_active_identity(db):
    first = save_case_context(db, _context(conversation_id=7, ticket_id=8), tenant_id="tenant-a")
    second = save_case_context(db, _context(conversation_id=7, ticket_id=8), tenant_id="tenant-b")

    assert first.id != second.id
    assert load_case_context(db, conversation_id=7, ticket_id=8, tenant_id="tenant-a").country_code == "ME"
    assert load_case_context(db, conversation_id=7, ticket_id=8, tenant_id="tenant-b").country_code == "ME"
    assert load_case_context(db, conversation_id=7, ticket_id=8, tenant_id="tenant-c") is None


def test_close_releases_identity_without_overwriting_history(db):
    original = save_case_context(db, _context(conversation_id=11, ticket_id=12))
    closed = close_case_context(db, conversation_id=11, ticket_id=12)

    assert closed is not None
    assert closed.id == original.id
    assert closed.is_active is False
    assert closed.status == CaseContextStatus.CLOSED.value
    assert load_case_context(db, conversation_id=11, ticket_id=12, tenant_id="default") is None

    replacement = save_case_context(db, _context(conversation_id=11, ticket_id=12))
    assert replacement.id != original.id
    assert replacement.is_active is True
    assert db.get(CaseContextRecord, original.id).status == CaseContextStatus.CLOSED.value


def test_expiry_releases_identity_and_save_cannot_reactivate_expired_row(db):
    original = save_case_context(
        db,
        _context(conversation_id=21, ticket_id=22),
        expires_at=utc_now() + timedelta(minutes=5),
    )
    expired = expire_case_context(db, conversation_id=21, ticket_id=22)

    assert expired is not None and expired.id == original.id and expired.is_active is False
    assert load_case_context(db, conversation_id=21, ticket_id=22, tenant_id="default") is None

    replacement = save_case_context(db, _context(conversation_id=21, ticket_id=22))
    assert replacement.id != original.id
    assert db.get(CaseContextRecord, original.id).is_active is False


def test_past_expiry_is_persisted_inactive_and_not_loaded(db):
    row = save_case_context(
        db,
        _context(conversation_id=31, ticket_id=32),
        expires_at=utc_now() - timedelta(seconds=1),
    )
    assert row.is_active is False
    assert load_case_context(db, conversation_id=31, ticket_id=32, tenant_id="default") is None

    replacement = save_case_context(db, _context(conversation_id=31, ticket_id=32))
    assert replacement.id != row.id


def test_closed_and_archived_rows_are_excluded_from_default_reads(db):
    closed = CaseContextRecord(
        tenant_id="default",
        conversation_id=41,
        ticket_id=None,
        status=CaseContextStatus.CLOSED.value,
        is_active=False,
    )
    archived = CaseContextRecord(
        tenant_id="default",
        conversation_id=None,
        ticket_id=42,
        status=CaseContextStatus.ARCHIVED.value,
        is_active=False,
    )
    db.add_all([closed, archived])
    db.flush()

    assert load_case_context(db, conversation_id=41, tenant_id="default") is None
    assert load_case_context(db, ticket_id=42, tenant_id="default") is None
    assert load_case_context(db, conversation_id=41, tenant_id="default", include_inactive=True).status == CaseContextStatus.CLOSED


def test_database_partial_indexes_reject_duplicate_active_exact_identity(db):
    db.add_all([
        CaseContextRecord(tenant_id="default", conversation_id=51, ticket_id=52, status="active", is_active=True),
        CaseContextRecord(tenant_id="default", conversation_id=51, ticket_id=52, status="active", is_active=True),
    ])
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    db.add_all([
        CaseContextRecord(tenant_id="default", conversation_id=51, ticket_id=52, status="closed", is_active=False),
        CaseContextRecord(tenant_id="default", conversation_id=51, ticket_id=52, status="archived", is_active=False),
    ])
    db.flush()


def test_migration_uses_stable_predicates_and_duplicate_preflight():
    migration = Path("backend/alembic/versions/20260710_0055_case_context_active_lifecycle.py").read_text(encoding="utf-8")

    assert 'revision = "20260710_0055"' in migration
    assert 'down_revision = "20260709_0054"' in migration
    assert "case_context_active_identity_duplicates_detected" in migration
    assert "case_context_legacy_unique_downgrade_blocked" in migration
    assert "row_ids" in migration
    assert "is_active IS TRUE" in migration
    assert "now()" not in migration.lower()
    assert "DELETE FROM case_contexts" not in migration.upper()
