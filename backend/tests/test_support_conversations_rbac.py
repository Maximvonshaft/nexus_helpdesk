from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/support_conversations_rbac_tests.db")
os.environ.setdefault("WEBCHAT_RATE_LIMIT_BACKEND", "memory")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.deps import get_current_user  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Market, Team, Tenant, Ticket, User  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def api_context(tmp_path):
    db_file = tmp_path / "support_conversations_rbac.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    state = {"user": None}

    def override_db():
        yield session

    def override_current_user():
        return state["user"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    client = TestClient(app)
    try:
        yield session, client, state
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _tenant(db, *, key: str) -> Tenant:
    row = Tenant(tenant_key=key, display_name=f"Tenant {key}", is_active=True)
    db.add(row)
    db.flush()
    return row


def _team(db, *, code: str, tenant: Tenant | None = None) -> Team:
    ownership = {"tenant_id": tenant.id} if tenant else {}
    market = Market(code=code, name=f"Market {code}", country_code=code, is_active=True, **ownership)
    db.add(market)
    db.flush()
    team = Team(name=f"Team {code}", team_type="support", market_id=market.id, is_active=True, **ownership)
    db.add(team)
    db.flush()
    return team


def _user(db, *, username: str, role: UserRole, team: Team | None, tenant: Tenant | None = None) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@invalid.test",
        password_hash="x",
        role=role,
        team_id=team.id if team else None,
        is_active=True,
        tenant_id=tenant.id if tenant else None,
    )
    db.add(row)
    db.flush()
    return row


def _conversation(
    db, *, suffix: str, team: Team, assignee: User | None = None, tenant: Tenant | None = None
) -> tuple[Ticket, WebchatConversation]:
    ownership = {"tenant_id": tenant.id} if tenant else {}
    customer = Customer(name=f"Customer {suffix}", phone=f"+4100000{suffix}", email=f"{suffix}@customer.test", **ownership)
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"SUP-{suffix}",
        title=f"Support {suffix}",
        description="RBAC fixture",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.ai_active,
        team_id=team.id,
        market_id=team.market_id,
        assignee_id=assignee.id if assignee else None,
        customer_request=f"request {suffix}",
        last_customer_message=f"message {suffix}",
        tracking_number=f"CH02000012{suffix}99",
        **ownership,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"wc_{suffix}",
        visitor_token_hash=hashlib.sha256(suffix.encode()).hexdigest(),
        tenant_key=tenant.tenant_key if tenant else f"tenant-{suffix}",
        channel_key="webchat",
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_phone=customer.phone,
        visitor_email=customer.email,
        origin="webchat-test",
        status="open",
        updated_at=utc_now(),
        last_seen_at=utc_now(),
    )
    db.add(conversation)
    db.flush()
    db.add(
        WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            direction="visitor",
            body=f"message {suffix}",
            body_text=f"message {suffix}",
            message_type="text",
            delivery_status="sent",
            author_label=customer.name,
        )
    )
    db.flush()
    return ticket, conversation


def test_agent_support_scope_blocks_other_team_list_detail_metrics_and_state(api_context):
    db, client, state = api_context
    team_a = _team(db, code="AA")
    team_b = _team(db, code="BB")
    agent = _user(db, username="agent-a", role=UserRole.agent, team=team_a)
    own_ticket, own_conversation = _conversation(db, suffix="1001", team=team_a, assignee=agent)
    _, hidden_conversation = _conversation(db, suffix="2002", team=team_b)
    db.commit()
    state["user"] = agent

    listing = client.get("/api/support/conversations", params={"view": "all"})
    assert listing.status_code == 200
    assert [item["session_key"] for item in listing.json()["items"]] == [f"webchat:{own_conversation.public_id}"]

    own_detail = client.get("/api/support/conversations/detail", params={"session_key": f"webchat:{own_conversation.public_id}"})
    assert own_detail.status_code == 200
    assert own_detail.json()["ticket"]["id"] == own_ticket.id

    hidden_detail = client.get("/api/support/conversations/detail", params={"session_key": f"webchat:{hidden_conversation.public_id}"})
    assert hidden_detail.status_code == 404
    assert hidden_detail.json()["detail"] == "support_conversation_not_found"

    metrics = client.get("/api/support/conversations/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["total"] == 1
    assert metrics.json()["runtime_latency"]["sample_count"] == 0

    state_payload = client.get("/api/support/conversations/state")
    assert state_payload.status_code == 200
    assert state_payload.json()["open"] == 1


def test_manager_scope_includes_same_market_but_not_other_market(api_context):
    db, client, state = api_context
    market_team = _team(db, code="CC")
    same_market_team = Team(name="Team CC Secondary", team_type="support", market_id=market_team.market_id, is_active=True)
    db.add(same_market_team)
    db.flush()
    other_team = _team(db, code="DD")
    manager = _user(db, username="manager-cc", role=UserRole.manager, team=market_team)
    _, direct = _conversation(db, suffix="3003", team=market_team)
    _, same_market = _conversation(db, suffix="3004", team=same_market_team)
    _, hidden = _conversation(db, suffix="4004", team=other_team)
    db.commit()
    state["user"] = manager

    listing = client.get("/api/support/conversations", params={"view": "all", "limit": 20})
    assert listing.status_code == 200
    keys = {item["session_key"] for item in listing.json()["items"]}
    assert keys == {f"webchat:{direct.public_id}", f"webchat:{same_market.public_id}"}
    assert f"webchat:{hidden.public_id}" not in keys


def test_admin_retains_explicit_global_support_view(api_context):
    db, client, state = api_context
    team_a = _team(db, code="EE")
    team_b = _team(db, code="FF")
    admin = _user(db, username="support-admin", role=UserRole.admin, team=None)
    _, first = _conversation(db, suffix="5005", team=team_a)
    _, second = _conversation(db, suffix="6006", team=team_b)
    db.commit()
    state["user"] = admin

    listing = client.get("/api/support/conversations", params={"view": "all"})
    assert listing.status_code == 200
    assert {item["session_key"] for item in listing.json()["items"]} == {
        f"webchat:{first.public_id}",
        f"webchat:{second.public_id}",
    }

def test_tenant_bound_admin_support_scope_is_not_global(api_context):
    db, client, state = api_context
    tenant_a = _tenant(db, key="tenant-a")
    tenant_b = _tenant(db, key="tenant-b")
    team_a = _team(db, code="TA", tenant=tenant_a)
    team_b = _team(db, code="TB", tenant=tenant_b)
    admin = _user(db, username="tenant-admin", role=UserRole.admin, team=None, tenant=tenant_a)
    ticket_a, conversation_a = _conversation(db, suffix="7007", team=team_a, tenant=tenant_a)
    _, conversation_b = _conversation(db, suffix="8008", team=team_b, tenant=tenant_b)
    db.commit()
    state["user"] = admin

    listing = client.get("/api/support/conversations", params={"view": "all"})
    assert listing.status_code == 200
    assert [item["session_key"] for item in listing.json()["items"]] == [
        f"webchat:{conversation_a.public_id}"
    ]

    own_detail = client.get(
        "/api/support/conversations/detail",
        params={"session_key": f"webchat:{conversation_a.public_id}"},
    )
    assert own_detail.status_code == 200
    assert own_detail.json()["ticket"]["id"] == ticket_a.id

    hidden_detail = client.get(
        "/api/support/conversations/detail",
        params={"session_key": f"webchat:{conversation_b.public_id}"},
    )
    assert hidden_detail.status_code == 404
    assert hidden_detail.json()["detail"] == "support_conversation_not_found"

    metrics = client.get("/api/support/conversations/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["total"] == 1
