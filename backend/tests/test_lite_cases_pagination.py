from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/lite_cases_pagination_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.lite import list_cases  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Customer, Team, Ticket, User  # noqa: E402
from app.services.lite_pagination import (  # noqa: E402
    _decode_cursor,
    _encode_cursor,
    _normalize_q,
    _safe_limit,
    list_lite_cases_page,
)


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "lite_cases.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_team(db, name: str) -> Team:
    row = Team(name=name)
    db.add(row)
    db.flush()
    return row


def make_user(db, username: str, role: UserRole = UserRole.admin, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@invalid.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_ticket(
    db,
    idx: int,
    *,
    status: TicketStatus,
    assignee_id: int | None,
    team_id: int | None,
    updated_at: datetime,
) -> Ticket:
    customer = Customer(name=f"Customer {idx}", email=f"customer{idx}@invalid.test")
    db.add(customer)
    db.flush()
    row = Ticket(
        ticket_no=f"LITE-{idx:03d}",
        title=f"Lite case {idx}",
        description="Lite pagination fixture",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=status,
        assignee_id=assignee_id,
        team_id=team_id,
        updated_at=updated_at,
    )
    db.add(row)
    db.flush()
    return row


def seed_tickets(db, *, total: int = 120):
    team_a = make_team(db, "team-a")
    team_b = make_team(db, "team-b")
    admin = make_user(db, "admin", UserRole.admin)
    agent_a = make_user(db, "agent-a", UserRole.agent, team_id=team_a.id)
    agent_b = make_user(db, "agent-b", UserRole.agent, team_id=team_b.id)
    base = datetime(2026, 5, 7, 12, 0, 0)
    rows = []
    for idx in range(total):
        rows.append(
            make_ticket(
                db,
                idx,
                status=TicketStatus.new if idx % 2 == 0 else TicketStatus.in_progress,
                assignee_id=agent_a.id if idx % 3 == 0 else agent_b.id,
                team_id=team_a.id if idx % 4 in {0, 1} else team_b.id,
                updated_at=base + timedelta(seconds=idx),
            )
        )
    db.commit()
    return admin, agent_a, agent_b, team_a, team_b, rows


def test_lite_limit_defaults_and_caps():
    assert _safe_limit(None) == 50
    assert _safe_limit(500) == 100
    assert _safe_limit(1) == 1


def test_lite_cursor_round_trip():
    updated_at = datetime(2026, 5, 7, 12, 30, tzinfo=timezone.utc)
    cursor = _encode_cursor(updated_at=updated_at, ticket_id=123)

    decoded_updated_at, decoded_id = _decode_cursor(cursor)

    assert decoded_updated_at == updated_at
    assert decoded_id == 123


def test_lite_invalid_cursor_returns_400():
    with pytest.raises(HTTPException) as exc:
        _decode_cursor("not-a-valid-cursor")

    assert exc.value.status_code == 400


def test_lite_q_search_bounds():
    assert _normalize_q("  abc  ") == "abc"
    with pytest.raises(HTTPException) as short_exc:
        _normalize_q("ab")
    with pytest.raises(HTTPException) as long_exc:
        _normalize_q("x" * 81)

    assert short_exc.value.status_code == 400
    assert long_exc.value.status_code == 400


def test_lite_cases_real_db_default_limit_cap_and_no_overlap(db_session):
    admin, *_ = seed_tickets(db_session, total=120)

    first = list_lite_cases_page(db_session, admin, limit=None)
    second = list_lite_cases_page(db_session, admin, cursor=first["next_cursor"], limit=None)
    capped = list_lite_cases_page(db_session, admin, limit=500)

    assert len(first["items"]) == 50
    assert first["filters"]["limit"] == 50
    assert first["has_more"] is True
    assert first["next_cursor"] is not None
    assert len(capped["items"]) == 100
    assert capped["filters"]["limit"] == 100
    assert not {item.id for item in first["items"]} & {item.id for item in second["items"]}


def test_lite_cases_real_db_status_assignee_and_team_cursor_stability(db_session):
    admin, agent_a, _agent_b, team_a, _team_b, _rows = seed_tickets(db_session, total=120)

    status_first = list_lite_cases_page(db_session, admin, status="new", limit=30)
    status_second = list_lite_cases_page(db_session, admin, status="new", cursor=status_first["next_cursor"], limit=30)
    assert status_first["next_cursor"] is not None
    assert not {item.id for item in status_first["items"]} & {item.id for item in status_second["items"]}
    assert all(item.status == "new" for item in [*status_first["items"], *status_second["items"]])

    assignee_first = list_lite_cases_page(db_session, admin, assignee_id=agent_a.id, limit=30)
    assignee_second = list_lite_cases_page(db_session, admin, assignee_id=agent_a.id, cursor=assignee_first["next_cursor"], limit=30)
    assert not {item.id for item in assignee_first["items"]} & {item.id for item in assignee_second["items"]}
    assignee_ids = {row.id for row in db_session.query(Ticket).filter(Ticket.assignee_id == agent_a.id).all()}
    assert {item.id for item in [*assignee_first["items"], *assignee_second["items"]]}.issubset(assignee_ids)

    team_first = list_lite_cases_page(db_session, admin, team_id=team_a.id, limit=30)
    team_second = list_lite_cases_page(db_session, admin, team_id=team_a.id, cursor=team_first["next_cursor"], limit=30)
    assert not {item.id for item in team_first["items"]} & {item.id for item in team_second["items"]}
    team_ids = {row.id for row in db_session.query(Ticket).filter(Ticket.team_id == team_a.id).all()}
    assert {item.id for item in [*team_first["items"], *team_second["items"]]}.issubset(team_ids)


def test_lite_cases_real_db_invalid_cursor_and_legacy_shape(db_session):
    admin, *_ = seed_tickets(db_session, total=120)

    with pytest.raises(HTTPException) as exc:
        list_lite_cases_page(db_session, admin, cursor="not-a-real-cursor")
    assert exc.value.status_code == 400

    legacy = list_cases(
        q=None,
        status=None,
        priority=None,
        assignee_id=None,
        team_id=None,
        overdue=None,
        cursor=None,
        limit=50,
        legacy=True,
        db=db_session,
        current_user=admin,
    )
    assert isinstance(legacy, list)
    assert legacy
    assert hasattr(legacy[0], "id")
    assert hasattr(legacy[0], "case")
    assert hasattr(legacy[0], "status")
