from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api import webchat_admin
from app.api.support_conversations import (
    list_support_conversations,
    resolve_support_conversation,
)
from app.enums import (
    ConversationState,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.model_registry import register_all_models
from app.models import Market, Team, Tenant, Ticket, User
from app.webchat_models import WebchatConversation, WebchatMessage

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


def _tenant_scope(db, suffix: str, label: str):
    tenant = Tenant(
        tenant_key=f"support-pg-{label}-{suffix}",
        display_name=f"Support PostgreSQL {label} {suffix}",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    market = Market(
        tenant_id=tenant.id,
        code=f"{label[:1].upper()}{suffix[:7]}",
        name=f"Support market {label} {suffix}",
        country_code="ZZ",
        is_active=True,
    )
    db.add(market)
    db.flush()
    team = Team(
        tenant_id=tenant.id,
        name=f"Support team {label} {suffix}",
        market_id=market.id,
        is_active=True,
    )
    db.add(team)
    db.flush()
    agent = User(
        tenant_id=tenant.id,
        username=f"support-pg-{label}-{suffix}",
        display_name=f"Support Agent {label}",
        email=f"support-pg-{label}-{suffix}@example.test",
        password_hash="x",
        role=UserRole.agent,
        team_id=team.id,
        is_active=True,
    )
    db.add(agent)
    db.flush()
    return tenant, market, team, agent


def _add_conversations(
    db,
    *,
    tenant: Tenant,
    market: Market,
    team: Team,
    assignee: User,
    suffix: str,
    label: str,
    count: int,
    now: datetime,
) -> list[tuple[int, str]]:
    created: list[tuple[int, str]] = []
    for index in range(count):
        created_at = now - timedelta(minutes=count - index)
        ticket = Ticket(
            tenant_id=tenant.id,
            ticket_no=f"SPG-{label}-{suffix}-{index:03d}",
            title=f"Representative support conversation {label} {index}",
            description="PostgreSQL representative-volume support fixture",
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.in_progress,
            assignee_id=assignee.id,
            team_id=team.id,
            market_id=market.id,
            country_code="ZZ",
            conversation_state=ConversationState.human_owned,
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(ticket)
        db.flush()
        public_id = f"spg-{label}-{suffix}-{index:03d}"
        conversation = WebchatConversation(
            public_id=public_id,
            visitor_token_hash=f"synthetic-{label}-{suffix}-{index}",
            tenant_key=tenant.tenant_key,
            channel_key="webchat",
            ticket_id=ticket.id,
            visitor_name=f"Private {label} Customer {index}",
            visitor_email=f"private-{label}-{index}@example.test",
            visitor_phone=f"+9900{index:08d}",
            status="open",
            created_at=created_at,
            updated_at=created_at,
            last_seen_at=created_at,
        )
        db.add(conversation)
        db.flush()
        db.add(
            WebchatMessage(
                conversation_id=conversation.id,
                ticket_id=ticket.id,
                direction="visitor",
                body=f"Raw private message {label} {index}",
                body_text=f"Raw private message {label} {index}",
                message_type="text",
                delivery_status="sent",
                author_label=conversation.visitor_name,
                created_at=created_at,
            )
        )
        created.append((ticket.id, public_id))
    db.flush()
    return created


def test_postgres_support_scope_idor_volume_and_query_count(
    pg_session,
    monkeypatch,
):
    db, connection = pg_session
    suffix = uuid4().hex[:10]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    tenant_a, market_a, team_a, agent_a = _tenant_scope(
        db,
        suffix,
        "alpha",
    )
    tenant_b, market_b, team_b, agent_b = _tenant_scope(
        db,
        suffix,
        "bravo",
    )
    visible = _add_conversations(
        db,
        tenant=tenant_a,
        market=market_a,
        team=team_a,
        assignee=agent_a,
        suffix=suffix,
        label="alpha",
        count=120,
        now=now,
    )
    hidden = _add_conversations(
        db,
        tenant=tenant_b,
        market=market_b,
        team=team_b,
        assignee=agent_b,
        suffix=suffix,
        label="bravo",
        count=120,
        now=now,
    )

    statement_count = 0

    def count_statement(*_args):
        nonlocal statement_count
        statement_count += 1

    event.listen(connection, "before_cursor_execute", count_statement)
    try:
        page = list_support_conversations(
            q=None,
            channel="all",
            view="all",
            limit=120,
            db=db,
            current_user=agent_a,
        )
    finally:
        event.remove(connection, "before_cursor_execute", count_statement)

    visible_ids = {ticket_id for ticket_id, _public_id in visible}
    hidden_ids = {ticket_id for ticket_id, _public_id in hidden}
    returned_ids = {item["ticket_id"] for item in page["items"]}
    assert returned_ids == visible_ids
    assert not (returned_ids & hidden_ids)
    assert len(page["items"]) == 120
    assert statement_count <= 8, statement_count
    serialized = str(page)
    assert "private-alpha-" not in serialized
    assert "Raw private message" not in serialized
    assert all(item["pii_minimized"] is True for item in page["items"])
    assert all(item["tracking_number"] is None for item in page["items"])

    hidden_ticket_id, hidden_public_id = hidden[0]
    with pytest.raises(HTTPException) as exc:
        resolve_support_conversation(
            session_key=f"webchat:{hidden_public_id}",
            db=db,
            current_user=agent_a,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "support_conversation_not_found"

    audit = Mock()
    memory = Mock(side_effect=AssertionError("hidden thread loaded memory"))
    monkeypatch.setattr(webchat_admin, "audit_sensitive_support_read", audit)
    monkeypatch.setattr(webchat_admin, "build_support_memory_ledger", memory)
    with pytest.raises(HTTPException) as exc:
        webchat_admin.get_webchat_thread(
            ticket_id=hidden_ticket_id,
            before_message_id=None,
            message_limit=100,
            db=db,
            current_user=agent_a,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "support_conversation_not_found"
    audit.assert_not_called()
    memory.assert_not_called()

    search = list_support_conversations(
        q="private-bravo-0@example.test",
        channel="all",
        view="all",
        limit=120,
        db=db,
        current_user=agent_a,
    )
    assert search["items"] == []
