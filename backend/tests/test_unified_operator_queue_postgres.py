from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.model_registry import register_all_models
from app.models import Market, Team, Ticket, User
from app.operator_models import OperatorQueueScopeGrant
from app.services.operator_work_queue import list_unified_operator_queue
from app.webchat_models import WebchatConversation

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires PostgreSQL DATABASE_URL",
)

register_all_models()


@pytest.fixture()
def pg_session():
    engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    db = Session()
    try:
        yield db, connection
    finally:
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_postgres_representative_volume_filter_and_cursor_are_complete_and_bounded(pg_session):
    db, connection = pg_session
    suffix = uuid4().hex[:12]
    tenant = f"queue-pg-{suffix}"
    country = "ZZ"
    channel = "webchat"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    market = Market(
        code=f"Q{suffix[:8]}",
        name=f"Queue market {suffix}",
        country_code=country,
        is_active=True,
    )
    db.add(market)
    db.flush()
    team = Team(name=f"Queue team {suffix}", market_id=market.id, is_active=True)
    db.add(team)
    db.flush()
    agent = User(
        username=f"queue-pg-{suffix}",
        display_name="Queue PostgreSQL Agent",
        email=f"queue-pg-{suffix}@example.test",
        password_hash="x",
        role=UserRole.agent,
        team_id=team.id,
        is_active=True,
    )
    db.add(agent)
    db.flush()
    db.add(
        OperatorQueueScopeGrant(
            user_id=agent.id,
            tenant_key=tenant,
            country_code=country,
            channel_key=channel,
            enabled=True,
            granted_by=None,
        )
    )
    db.flush()

    expected: set[int] = set()
    for index in range(150):
        priority = TicketPriority.urgent if index >= 120 else TicketPriority.low
        created_at = now - timedelta(minutes=150 - index)
        ticket = Ticket(
            ticket_no=f"QPG-{suffix}-{index}",
            title="Synthetic queue volume item",
            description="Synthetic queue volume item",
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=priority,
            status=TicketStatus.pending_assignment,
            team_id=team.id,
            country_code=country,
            conversation_state=ConversationState.human_review_required,
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(ticket)
        db.flush()
        db.add(
            WebchatConversation(
                public_id=f"qpg-{suffix}-{index}",
                visitor_token_hash=f"synthetic-{suffix}-{index}",
                tenant_key=tenant,
                channel_key=channel,
                ticket_id=ticket.id,
                status="open",
                created_at=created_at,
                updated_at=created_at,
                last_seen_at=created_at,
            )
        )
        if priority == TicketPriority.urgent:
            expected.add(ticket.id)
    db.flush()

    statement_count = 0

    def count_statement(*_args):
        nonlocal statement_count
        statement_count += 1

    event.listen(connection, "before_cursor_execute", count_statement)
    try:
        seen: list[int] = []
        cursor = None
        for _ in range(10):
            page = list_unified_operator_queue(
                db,
                current_user=agent,
                tenant_key=tenant,
                country_code=country,
                channel_key=channel,
                source_type="ticket",
                priority="urgent",
                sort="oldest",
                cursor=cursor,
                limit=17,
            )
            seen.extend(item["source_id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
    finally:
        event.remove(connection, "before_cursor_execute", count_statement)

    assert set(seen) == expected
    assert len(seen) == len(expected) == 30
    assert len(seen) == len(set(seen))
    # Permission, exact scope and data page queries remain
    # constant per page; volume must not introduce an N+1 pattern.
    assert statement_count <= 16, statement_count
