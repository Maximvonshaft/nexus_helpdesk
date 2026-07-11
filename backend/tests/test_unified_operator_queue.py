from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/unified_operator_queue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import (
    ConversationState,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.model_registry import register_all_models
from app.models import AdminAuditLog, Market, Team, Ticket, User
from app.models_operations_dispatch import OperationsDispatchOutboxRecord
from app.models_osr import WhatsAppRoutingRuleRecord
from app.operator_models import OperatorQueueScopeGrant, OperatorTask
from app.operator_schemas import OperatorQueueScopeGrantUpsert, UnifiedOperatorQueueResponse
from app.services.operator_queue_scope import delete_scope_grant, upsert_scope_grant
from app.services.operator_work_queue import list_unified_operator_queue
from app.webchat_models import WebchatConversation, WebchatHandoffRequest

register_all_models()

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TENANT = "tenant-queue-a"
COUNTRY = "ME"
CHANNEL = "webchat"
SENSITIVE_SENTINEL = "customer-secret@example.test +38267000111 TRACK-PRIVATE"


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'unified_operator_queue.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _user(db, *, username: str, role: UserRole, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _team(db, country: str = COUNTRY) -> tuple[Market, Team]:
    market = Market(code=f"M-{country}", name=f"Market {country}", country_code=country, is_active=True)
    db.add(market)
    db.flush()
    team = Team(name=f"Team {country}", market_id=market.id, is_active=True)
    db.add(team)
    db.flush()
    return market, team


def _grant(db, *, admin: User, user: User, tenant: str = TENANT, country: str = COUNTRY, channel: str = CHANNEL):
    payload = OperatorQueueScopeGrantUpsert(
        user_id=user.id,
        tenant_key=tenant,
        country_code=country,
        channel_key=channel,
        enabled=True,
    )
    return upsert_scope_grant(db, current_user=admin, payload=payload)


def _ticket(
    db,
    *,
    suffix: str,
    team_id: int | None,
    assignee_id: int | None = None,
    priority: TicketPriority = TicketPriority.high,
    status: TicketStatus = TicketStatus.pending_assignment,
    created_at: datetime = NOW,
) -> Ticket:
    row = Ticket(
        ticket_no=f"QUEUE-{suffix}",
        title=SENSITIVE_SENTINEL,
        description=SENSITIVE_SENTINEL,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=priority,
        status=status,
        assignee_id=assignee_id,
        team_id=team_id,
        country_code=COUNTRY,
        conversation_state=ConversationState.human_review_required,
        tracking_number=SENSITIVE_SENTINEL,
        first_response_due_at=NOW + timedelta(minutes=20),
        required_action=SENSITIVE_SENTINEL,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def _conversation(db, *, ticket: Ticket, tenant: str = TENANT, channel: str = CHANNEL, suffix: str) -> WebchatConversation:
    row = WebchatConversation(
        public_id=f"public-{suffix}",
        visitor_token_hash=f"hash-{suffix}",
        tenant_key=tenant,
        channel_key=channel,
        ticket_id=ticket.id,
        visitor_name=SENSITIVE_SENTINEL,
        visitor_email=SENSITIVE_SENTINEL,
        visitor_phone=SENSITIVE_SENTINEL,
        status="open",
        created_at=ticket.created_at,
        updated_at=ticket.created_at,
        last_seen_at=ticket.created_at,
    )
    db.add(row)
    db.flush()
    return row


def _handoff(db, *, ticket: Ticket, conversation: WebchatConversation, suffix: str, assigned_agent_id: int | None = None):
    row = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        source="ai_auto",
        trigger_type="handoff_required",
        status="requested",
        reason_text=SENSITIVE_SENTINEL,
        recommended_agent_action=SENSITIVE_SENTINEL,
        assigned_agent_id=assigned_agent_id,
        requested_at=ticket.created_at,
        created_at=ticket.created_at,
        updated_at=ticket.created_at,
    )
    db.add(row)
    db.flush()
    return row


def _dispatch(db, *, ticket: Ticket | None, created_at: datetime = NOW, status: str = "retryable"):
    rule = WhatsAppRoutingRuleRecord(
        country_code=COUNTRY,
        issue_type=f"queue-{ticket.id if ticket else 'orphan'}-{created_at.timestamp()}",
        channel=CHANNEL,
        destination_group_id=SENSITIVE_SENTINEL,
        enabled=True,
    )
    db.add(rule)
    db.flush()
    row = OperationsDispatchOutboxRecord(
        ticket_id=ticket.id if ticket else None,
        dispatch_key=f"dispatch-{rule.id}",
        tenant_key=TENANT,
        country_code=COUNTRY,
        channel_key=CHANNEL,
        routing_rule_id=rule.id,
        destination_group_key=SENSITIVE_SENTINEL,
        destination_group_hash="hash-only",
        status=status,
        attempt_count=2,
        max_attempts=5,
        next_retry_at=created_at + timedelta(minutes=5) if status == "retryable" else None,
        error_category="provider_timeout",
        error_summary_redacted=SENSITIVE_SENTINEL,
        provider_acknowledgement=SENSITIVE_SENTINEL,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def _seed_all(db):
    _, team = _team(db)
    admin = _user(db, username="queue-admin", role=UserRole.admin)
    agent = _user(db, username="queue-agent", role=UserRole.agent, team_id=team.id)
    _grant(db, admin=admin, user=agent)
    ticket = _ticket(db, suffix="A", team_id=team.id)
    conversation = _conversation(db, ticket=ticket, suffix="a")
    handoff = _handoff(db, ticket=ticket, conversation=conversation, suffix="a")
    dispatch = _dispatch(db, ticket=ticket)
    db.commit()
    return admin, agent, ticket, conversation, handoff, dispatch


def _list(db, user, **kwargs):
    return list_unified_operator_queue(
        db,
        current_user=user,
        tenant_key=kwargs.pop("tenant_key", TENANT),
        country_code=kwargs.pop("country_code", COUNTRY),
        channel_key=kwargs.pop("channel_key", CHANNEL),
        **kwargs,
    )


def test_live_union_returns_three_authoritative_sources_without_projection_write(db_session):
    _, agent, *_ = _seed_all(db_session)
    before = {
        "operator_tasks": db_session.query(OperatorTask).count(),
        "tickets": db_session.query(Ticket).count(),
        "handoffs": db_session.query(WebchatHandoffRequest).count(),
        "dispatches": db_session.query(OperationsDispatchOutboxRecord).count(),
    }

    result = _list(db_session, agent)

    assert [item["source_type"] for item in result["items"]] == ["handoff", "ticket", "dispatch"]
    assert UnifiedOperatorQueueResponse.model_validate(result)
    assert before == {
        "operator_tasks": db_session.query(OperatorTask).count(),
        "tickets": db_session.query(Ticket).count(),
        "handoffs": db_session.query(WebchatHandoffRequest).count(),
        "dispatches": db_session.query(OperationsDispatchOutboxRecord).count(),
    }


def test_response_is_bounded_and_contains_no_customer_or_provider_secrets(db_session):
    _, agent, _, _, handoff, dispatch = _seed_all(db_session)
    dispatch.error_category = SENSITIVE_SENTINEL
    handoff.status = SENSITIVE_SENTINEL
    db_session.commit()
    serialized = UnifiedOperatorQueueResponse.model_validate(_list(db_session, agent)).model_dump_json()
    assert SENSITIVE_SENTINEL not in serialized
    for forbidden in ("visitor_email", "tracking_number", "lease_owner", "destination_group", "error_summary", "provider_acknowledgement"):
        assert forbidden not in serialized
    assert TENANT not in serialized
    assert "redacted_error_category" in serialized
    assert next(item for item in _list(db_session, agent)["items"] if item["source_type"] == "handoff")["source_status"] == "unknown"


def test_non_admin_requires_exact_active_scope_grant(db_session):
    _, team = _team(db_session)
    agent = _user(db_session, username="no-grant", role=UserRole.agent, team_id=team.id)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        _list(db_session, agent)
    assert exc.value.status_code == 403
    assert exc.value.detail == "operator_queue_scope_not_granted"


def test_forged_tenant_scope_is_denied_even_with_other_grant(db_session):
    _, agent, *_ = _seed_all(db_session)
    with pytest.raises(HTTPException) as exc:
        _list(db_session, agent, tenant_key="tenant-forged")
    assert exc.value.status_code == 403


def test_team_country_intersection_cannot_be_expanded_by_grant(db_session):
    admin, agent, *_ = _seed_all(db_session)
    _grant(db_session, admin=admin, user=agent, country="CH")
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        _list(db_session, agent, country_code="CH")
    assert exc.value.detail == "operator_queue_team_scope_mismatch"


def test_grant_does_not_expand_ticket_team_visibility(db_session):
    _, own_team = _team(db_session)
    other_market = Market(code="M-OTHER", name="Other same-country market", country_code=COUNTRY, is_active=True)
    db_session.add(other_market)
    db_session.flush()
    other_team = Team(name="Other team", market_id=other_market.id, is_active=True)
    db_session.add(other_team)
    db_session.flush()
    admin = _user(db_session, username="scope-admin", role=UserRole.admin)
    agent = _user(db_session, username="scope-agent", role=UserRole.agent, team_id=own_team.id)
    _grant(db_session, admin=admin, user=agent)
    ticket = _ticket(db_session, suffix="OTHER", team_id=other_team.id)
    _conversation(db_session, ticket=ticket, suffix="other")
    db_session.commit()
    assert _list(db_session, agent)["items"] == []


def test_cross_tenant_ticket_provenance_fails_closed(db_session):
    _, agent, ticket, *_ = _seed_all(db_session)
    _conversation(db_session, ticket=ticket, tenant="tenant-conflict", suffix="conflict")
    db_session.commit()

    result = _list(db_session, agent)

    assert result["items"] == []


def test_orphan_dispatch_remains_visible_only_in_its_exact_scope(db_session):
    admin, agent, *_ = _seed_all(db_session)
    orphan = _dispatch(db_session, ticket=None, created_at=NOW + timedelta(seconds=1))
    db_session.commit()
    result = _list(db_session, agent, source_type="dispatch")
    assert {item["source_id"] for item in result["items"]} == {orphan.id, 1}
    assert next(item for item in result["items"] if item["source_id"] == orphan.id)["case_key"] is None


def test_filters_cover_state_source_owner_priority_sla_and_retry(db_session):
    _, agent, ticket, *_ = _seed_all(db_session)
    ticket.assignee_id = agent.id
    db_session.commit()
    assert {item["source_type"] for item in _list(db_session, agent, source_type="handoff")["items"]} == {"handoff"}
    assert all(item["state"] == "active" for item in _list(db_session, agent, state="active")["items"])
    assert all(item["priority"] == "high" for item in _list(db_session, agent, priority="high")["items"])
    assert {item["source_type"] for item in _list(db_session, agent, owner="mine")["items"]} == {"handoff", "ticket", "dispatch"}
    assert all(item["sla"]["state"] == "at_risk" for item in _list(db_session, agent, sla="at_risk")["items"])
    retry = _list(db_session, agent, retry="retry_scheduled")
    assert [item["source_type"] for item in retry["items"]] == ["dispatch"]


def test_stale_and_reopened_states_are_explicit(db_session):
    _, agent, ticket, *_ = _seed_all(db_session)
    ticket.first_response_due_at = None
    ticket.resolution_due_at = None
    ticket.reopen_count = 2
    ticket.updated_at = NOW - timedelta(days=2)
    conversation = db_session.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id).one()
    conversation.updated_at = NOW - timedelta(days=2)
    handoff = db_session.query(WebchatHandoffRequest).filter(WebchatHandoffRequest.ticket_id == ticket.id).one()
    handoff.updated_at = NOW - timedelta(days=2)
    dispatch = db_session.query(OperationsDispatchOutboxRecord).filter(OperationsDispatchOutboxRecord.ticket_id == ticket.id).one()
    dispatch.updated_at = NOW - timedelta(days=2)
    db_session.commit()

    result = _list(db_session, agent, sla="stale")

    assert {item["source_type"] for item in result["items"]} == {"handoff", "ticket", "dispatch"}
    assert all(item["sla"]["state"] == "stale" for item in result["items"])
    assert all(item["reopened"] is True for item in result["items"])


def test_stable_cursor_has_no_duplicates_and_is_bound_to_actor_filters_and_grant(db_session):
    admin, agent, *_ = _seed_all(db_session)
    page1 = _list(db_session, agent, limit=2)
    assert len(page1["items"]) == 2
    assert page1["next_cursor"]
    page2 = _list(db_session, agent, limit=2, cursor=page1["next_cursor"])
    assert len(page2["items"]) == 1
    assert {item["queue_id"] for item in page1["items"]}.isdisjoint({item["queue_id"] for item in page2["items"]})

    other = _user(db_session, username="other-cursor", role=UserRole.agent, team_id=agent.team_id)
    _grant(db_session, admin=admin, user=other)
    db_session.commit()
    with pytest.raises(HTTPException) as actor_exc:
        _list(db_session, other, limit=2, cursor=page1["next_cursor"])
    assert actor_exc.value.detail == "operator_queue_cursor_context_mismatch"
    with pytest.raises(HTTPException) as filter_exc:
        _list(db_session, agent, limit=2, state="active", cursor=page1["next_cursor"])
    assert filter_exc.value.detail == "operator_queue_cursor_context_mismatch"

    same_country_market = Market(code="M-CURSOR", name="Cursor market", country_code=COUNTRY, is_active=True)
    db_session.add(same_country_market)
    db_session.flush()
    new_team = Team(name="Cursor team", market_id=same_country_market.id, is_active=True)
    db_session.add(new_team)
    db_session.flush()
    agent.team_id = new_team.id
    db_session.commit()
    with pytest.raises(HTTPException) as changed_auth:
        _list(db_session, agent, limit=2, cursor=page1["next_cursor"])
    assert changed_auth.value.detail == "operator_queue_cursor_context_mismatch"

    grant = db_session.query(OperatorQueueScopeGrant).filter(OperatorQueueScopeGrant.user_id == agent.id).one()
    grant.enabled = False
    grant.updated_at = NOW + timedelta(minutes=1)
    db_session.commit()
    with pytest.raises(HTTPException) as revoked:
        _list(db_session, agent, limit=2, cursor=page1["next_cursor"])
    assert revoked.value.detail == "operator_queue_scope_not_granted"


@pytest.mark.parametrize("sort", ["oldest", "newest"])
def test_cursor_continues_across_more_than_one_source_fetch_window(db_session, sort):
    _, team = _team(db_session)
    admin = _user(db_session, username=f"paging-admin-{sort}", role=UserRole.admin)
    agent = _user(db_session, username=f"paging-agent-{sort}", role=UserRole.agent, team_id=team.id)
    _grant(db_session, admin=admin, user=agent)
    expected: set[str] = set()
    for index in range(12):
        created = NOW + timedelta(seconds=index)
        ticket = _ticket(db_session, suffix=f"PAGE-{sort}-{index}", team_id=team.id, created_at=created)
        conversation = _conversation(db_session, ticket=ticket, suffix=f"page-{sort}-{index}")
        _handoff(db_session, ticket=ticket, conversation=conversation, suffix=f"page-{index}")
        expected.update({f"handoff:{index + 1}", f"ticket:{ticket.id}"})
    db_session.commit()

    seen: list[str] = []
    cursor = None
    for _ in range(20):
        page = _list(db_session, agent, sort=sort, limit=3, cursor=cursor)
        seen.extend(item["queue_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(expected)
    assert len(seen) == len(set(seen))
    assert set(seen) == expected


def test_sql_filters_do_not_lose_match_after_large_nonmatching_prefix(db_session):
    _, team = _team(db_session)
    admin = _user(db_session, username="filter-admin", role=UserRole.admin)
    agent = _user(db_session, username="filter-agent", role=UserRole.agent, team_id=team.id)
    _grant(db_session, admin=admin, user=agent)
    prefix_start = NOW - timedelta(seconds=200)
    for index in range(250):
        ticket = _ticket(
            db_session,
            suffix=f"LOW-{index}",
            team_id=team.id,
            priority=TicketPriority.low,
            created_at=prefix_start + timedelta(seconds=index),
        )
        _conversation(db_session, ticket=ticket, suffix=f"low-{index}")
    urgent = _ticket(
        db_session,
        suffix="URGENT-AFTER-PREFIX",
        team_id=team.id,
        priority=TicketPriority.urgent,
        created_at=NOW - timedelta(seconds=100),
    )
    _conversation(db_session, ticket=urgent, suffix="urgent-after-prefix")
    db_session.commit()

    result = _list(db_session, agent, source_type="ticket", priority="urgent", limit=10)

    assert [item["source_id"] for item in result["items"]] == [urgent.id]
    assert result["next_cursor"] is None


def test_many_same_scope_conversations_cannot_monopolize_ticket_page(db_session):
    _, team = _team(db_session)
    admin = _user(db_session, username="conversation-admin", role=UserRole.admin)
    agent = _user(db_session, username="conversation-agent", role=UserRole.agent, team_id=team.id)
    _grant(db_session, admin=admin, user=agent)
    first = _ticket(db_session, suffix="MULTI-CONV", team_id=team.id, created_at=NOW)
    for index in range(250):
        _conversation(db_session, ticket=first, suffix=f"many-{index}")
    second = _ticket(db_session, suffix="AFTER-MULTI", team_id=team.id, created_at=NOW + timedelta(seconds=1))
    _conversation(db_session, ticket=second, suffix="after-many")
    db_session.commit()

    first_page = _list(db_session, agent, source_type="ticket", limit=1)
    second_page = _list(db_session, agent, source_type="ticket", limit=1, cursor=first_page["next_cursor"])

    assert [item["source_id"] for item in first_page["items"]] == [first.id]
    assert [item["source_id"] for item in second_page["items"]] == [second.id]
    assert second_page["next_cursor"] is None


def test_ticket_with_exact_outbox_provenance_but_no_webchat_is_included(db_session):
    _, team = _team(db_session)
    admin = _user(db_session, username="outbox-admin", role=UserRole.admin)
    agent = _user(db_session, username="outbox-agent", role=UserRole.agent, team_id=team.id)
    _grant(db_session, admin=admin, user=agent)
    ticket = _ticket(db_session, suffix="OUTBOX-ONLY", team_id=team.id)
    dispatch = _dispatch(db_session, ticket=ticket)
    no_provenance = _ticket(db_session, suffix="NO-PROVENANCE", team_id=team.id, created_at=NOW + timedelta(seconds=1))
    db_session.commit()

    result = _list(db_session, agent, source_type="ticket")

    assert [item["source_id"] for item in result["items"]] == [ticket.id]
    assert result["items"][0]["conversation_id"] is None
    assert no_provenance.id not in {item["source_id"] for item in result["items"]}
    assert dispatch.ticket_id == ticket.id


def test_source_links_only_reference_existing_safe_routes(db_session):
    _, agent, *_ = _seed_all(db_session)
    by_source = {item["source_type"]: item for item in _list(db_session, agent)["items"]}
    assert by_source["ticket"]["source_links"]["ticket"].startswith("/api/tickets/")
    assert by_source["handoff"]["source_links"]["conversation"].startswith("/api/webchat/admin/tickets/")
    assert by_source["handoff"]["source_links"]["handoff"] == "/api/webchat/admin/handoff/queue"
    assert by_source["dispatch"]["source_links"]["dispatch"] is None
    serialized = UnifiedOperatorQueueResponse.model_validate(_list(db_session, agent)).model_dump_json()
    assert "/api/admin/webchat/" not in serialized
    assert "/api/admin/nexus-osr/" not in serialized


@pytest.mark.parametrize("cursor", ["not-base64!", "e30", "A" * 2049])
def test_malformed_or_oversized_cursor_fails_closed(db_session, cursor):
    _, agent, *_ = _seed_all(db_session)
    with pytest.raises(HTTPException) as exc:
        _list(db_session, agent, cursor=cursor)
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_operator_queue_cursor"


def test_cursor_tampering_fails_signature_check(db_session):
    _, agent, *_ = _seed_all(db_session)
    cursor = _list(db_session, agent, limit=1)["next_cursor"]
    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    with pytest.raises(HTTPException) as exc:
        _list(db_session, agent, limit=1, cursor=tampered)
    assert exc.value.detail == "invalid_operator_queue_cursor"


def test_scope_grant_crud_is_normalized_hashed_and_audited(db_session):
    _, team = _team(db_session)
    admin = _user(db_session, username="grant-admin", role=UserRole.admin)
    agent = _user(db_session, username="grant-agent", role=UserRole.agent, team_id=team.id)
    grant = _grant(db_session, admin=admin, user=agent, tenant="Tenant.Example", country="me", channel="WebChat")
    assert grant.country_code == "ME"
    assert grant.channel_key == "webchat"
    audit = db_session.query(AdminAuditLog).filter(AdminAuditLog.target_id == grant.id).one()
    assert "Tenant.Example" not in (audit.new_value_json or "")
    assert "tenant_hash" in (audit.new_value_json or "")

    delete_scope_grant(db_session, current_user=admin, grant_id=grant.id)
    db_session.flush()
    assert db_session.query(OperatorQueueScopeGrant).count() == 0
    assert db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "operator_queue.scope_grant.deleted").count() == 1


def test_grant_schema_forbids_unknown_fields_and_wildcards(db_session):
    with pytest.raises(ValidationError):
        OperatorQueueScopeGrantUpsert.model_validate(
            {"user_id": 1, "tenant_key": TENANT, "country_code": COUNTRY, "channel_key": CHANNEL, "extra": True}
        )
    _, team = _team(db_session)
    admin = _user(db_session, username="wild-admin", role=UserRole.admin)
    agent = _user(db_session, username="wild-agent", role=UserRole.agent, team_id=team.id)
    with pytest.raises(ValidationError):
        OperatorQueueScopeGrantUpsert(user_id=agent.id, tenant_key=TENANT, country_code="*", channel_key="*")


def test_legacy_projection_table_is_not_used_as_unified_source(db_session):
    _, agent, *_ = _seed_all(db_session)
    db_session.add(
        OperatorTask(
            source_type="legacy",
            source_id="secret",
            task_type="legacy",
            status="pending",
            priority=1,
            payload_json=SENSITIVE_SENTINEL,
        )
    )
    db_session.commit()
    result = _list(db_session, agent)
    assert "legacy" not in {item["source_type"] for item in result["items"]}
    assert SENSITIVE_SENTINEL not in UnifiedOperatorQueueResponse.model_validate(result).model_dump_json()
