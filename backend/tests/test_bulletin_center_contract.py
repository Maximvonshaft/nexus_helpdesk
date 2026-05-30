from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import operator_models as _operator_models  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, Customer, Market, MarketBulletin, Ticket, User  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _user(db_session, username: str, role: UserRole) -> User:
    row = User(
        username=username,
        display_name=username.title(),
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(
    db_session,
    *,
    ticket_no: str,
    market_id: int,
    country_code: str,
    channel: SourceChannel,
    status: TicketStatus,
    conversation_state: ConversationState = ConversationState.ai_active,
    preferred_reply_channel: str | None = None,
) -> Ticket:
    customer = Customer(name=f"Customer {ticket_no}", email=f"{ticket_no.lower()}@example.test")
    db_session.add(customer)
    db_session.flush()
    row = Ticket(
        ticket_no=ticket_no,
        title=f"{ticket_no} delivery issue",
        description="delivery issue",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=channel,
        priority=TicketPriority.medium,
        status=status,
        market_id=market_id,
        country_code=country_code,
        conversation_state=conversation_state,
        preferred_reply_channel=preferred_reply_channel,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_bulletin_center_impact_preview_and_audited_writes(tmp_path):
    db_file = tmp_path / "bulletins.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    market = Market(code="PH", name="Philippines", country_code="PH")
    db_session.add(market)
    db_session.flush()
    admin = _user(db_session, "admin_bulletin", UserRole.admin)
    agent = _user(db_session, "agent_bulletin", UserRole.agent)
    _ticket(
        db_session,
        ticket_no="BUL-001",
        market_id=market.id,
        country_code="PH",
        channel=SourceChannel.email,
        preferred_reply_channel="email",
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.ready_to_reply,
    )
    _ticket(
        db_session,
        ticket_no="BUL-002",
        market_id=market.id,
        country_code="PH",
        channel=SourceChannel.web_chat,
        preferred_reply_channel="webchat",
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
    )
    _ticket(
        db_session,
        ticket_no="BUL-003",
        market_id=market.id,
        country_code="PH",
        channel=SourceChannel.email,
        preferred_reply_channel="email",
        status=TicketStatus.closed,
        conversation_state=ConversationState.replied_to_customer,
    )
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        preview = client.post(
            "/api/admin/bulletins/impact-preview",
            headers=_headers(admin),
            json={
                "market_id": market.id,
                "country_code": "ph",
                "channels_csv": "email, webchat",
                "audience": "customer",
                "auto_inject_to_ai": True,
                "is_active": True,
            },
        )
        forbidden_preview = client.post(
            "/api/admin/bulletins/impact-preview",
            headers=_headers(agent),
            json={"market_id": market.id},
        )
        created = client.post(
            "/api/admin/bulletins",
            headers=_headers(admin),
            json={
                "market_id": market.id,
                "country_code": "ph",
                "title": "港口延误公告",
                "body": "预计末端处理延迟，请先安抚客户。",
                "summary": "PH 延误统一口径",
                "category": "delay",
                "channels_csv": "email,webchat",
                "audience": "customer",
                "severity": "warning",
                "auto_inject_to_ai": True,
                "is_active": True,
            },
        )
        updated = client.patch(
            f"/api/admin/bulletins/{created.json()['id']}",
            headers=_headers(admin),
            json={"market_id": None, "country_code": "us", "severity": "critical", "is_active": False},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["matching_tickets"] == 2
    assert payload["ready_to_reply_tickets"] == 2
    assert payload["scope_label"] == f"market:{market.id} · country:PH · channels:email,web_chat"
    assert payload["ai_context_enabled"] is True
    assert {row["channel"]: row["count"] for row in payload["channel_counts"]} == {"email": 1, "web_chat": 1}
    assert [row["ticket_no"] for row in payload["sample_tickets"]] == ["BUL-002", "BUL-001"]
    assert forbidden_preview.status_code == 403

    assert created.status_code == 200, created.text
    assert created.json()["country_code"] == "PH"
    assert updated.status_code == 200, updated.text
    assert updated.json()["market_id"] is None
    assert updated.json()["country_code"] == "US"
    assert updated.json()["severity"] == "critical"
    assert updated.json()["is_active"] is False

    audits = db_session.query(AdminAuditLog).filter(AdminAuditLog.target_type == "market_bulletin").order_by(AdminAuditLog.id.asc()).all()
    assert [row.action for row in audits] == ["bulletin.create", "bulletin.update"]
    create_new = json.loads(audits[0].new_value_json or "{}")
    update_old = json.loads(audits[1].old_value_json or "{}")
    update_new = json.loads(audits[1].new_value_json or "{}")
    assert create_new["country_code"] == "PH"
    assert create_new["channels"] == ["email", "web_chat"]
    assert update_old["market_id"] == market.id
    assert update_new["market_id"] is None
    assert update_new["country_code"] == "US"
    assert update_new["is_active"] is False

    db_session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()
