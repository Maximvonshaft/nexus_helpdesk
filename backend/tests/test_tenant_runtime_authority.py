from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/tenant-runtime-authority-tests.db")

from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.models import ChannelAccount, Customer, Market, Team, Tenant, Ticket, User
from app.schemas import CustomerInput, LiteCaseCreate, TicketCreate
from app.services.lite_pagination import list_lite_cases_page
from app.services.lite_service import create_lite_case
from app.services.permissions import ensure_ticket_visible
from app.services.ticket_service import create_ticket, list_tickets
from app.services.tenant_authority import (
    RUNTIME_TENANT_ASSIGNMENT_SOURCE,
    RUNTIME_TENANT_ASSIGNMENT_VERSION,
    ensure_ticket_tenant_authority,
    resolve_actor_tenant_id,
)
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _settings_cache(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TENANT_RUNTIME_AUTHORITY_MODE", "shadow")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tenant-runtime.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _tenant(db, key: str) -> Tenant:
    row = Tenant(tenant_key=key, display_name=key.title(), is_active=True)
    db.add(row)
    db.flush()
    return row


def _org(db, tenant: Tenant, suffix: str):
    ownership = {
        "tenant_id": tenant.id,
        "tenant_assignment_source": "fixture",
        "tenant_assignment_version": "sha256:" + (suffix[0] * 64),
    }
    market = Market(
        code=f"M-{suffix}",
        name=f"Market {suffix}",
        country_code="ME",
        **ownership,
    )
    db.add(market)
    db.flush()
    team = Team(name=f"Team {suffix}", market_id=market.id, **ownership)
    db.add(team)
    db.flush()
    return market, team, ownership


def _user(db, tenant: Tenant | None, team: Team | None, username: str, role=UserRole.admin) -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@invalid.test",
        password_hash="x",
        role=role,
        team_id=team.id if team else None,
        tenant_id=tenant.id if tenant else None,
        tenant_assignment_source="fixture" if tenant else None,
        tenant_assignment_version=("sha256:" + ("u" * 64)) if tenant else None,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _ticket(
    db,
    *,
    tenant: Tenant | None,
    market: Market | None,
    team: Team | None,
    actor: User | None,
    suffix: str,
    customer: Customer | None = None,
    assignee: User | None = None,
    channel: ChannelAccount | None = None,
) -> Ticket:
    row = Ticket(
        ticket_no=f"TEN-{suffix}",
        title=f"Ticket {suffix}",
        description="Tenant runtime fixture",
        source=TicketSource.manual,
        source_channel=SourceChannel.internal,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        tenant_id=tenant.id if tenant else None,
        tenant_assignment_source="fixture" if tenant else None,
        tenant_assignment_version=("sha256:" + ("t" * 64)) if tenant else None,
        market_id=market.id if market else None,
        team_id=team.id if team else None,
        customer_id=customer.id if customer else None,
        assignee_id=assignee.id if assignee else None,
        created_by=actor.id if actor else None,
        channel_account_id=channel.id if channel else None,
    )
    db.add(row)
    db.flush()
    return row


def test_enforce_mode_rejects_authenticated_user_without_relational_tenant(db_session, monkeypatch):
    monkeypatch.setenv("TENANT_RUNTIME_AUTHORITY_MODE", "enforce")
    get_settings.cache_clear()
    actor = _user(db_session, None, None, "legacy-admin")

    with pytest.raises(HTTPException) as exc:
        resolve_actor_tenant_id(db_session, actor)

    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "tenant_principal_missing"


def test_shadow_mode_preserves_fully_legacy_unowned_rows(db_session):
    actor = _user(db_session, None, None, "legacy-manager", UserRole.manager)
    ticket = _ticket(
        db_session,
        tenant=None,
        market=None,
        team=None,
        actor=actor,
        suffix="LEGACY",
    )

    ensure_ticket_visible(actor, ticket, db_session)
    assert list_tickets(db_session, actor) == [ticket]


def test_privileged_role_cannot_read_another_tenant_ticket(db_session):
    tenant_a = _tenant(db_session, "tenant-a")
    tenant_b = _tenant(db_session, "tenant-b")
    market_a, team_a, _ = _org(db_session, tenant_a, "A")
    market_b, team_b, ownership_b = _org(db_session, tenant_b, "B")
    actor = _user(db_session, tenant_a, team_a, "tenant-a-admin", UserRole.admin)
    creator_b = _user(db_session, tenant_b, team_b, "tenant-b-admin", UserRole.admin)
    customer_b = Customer(name="Tenant B Customer", **ownership_b)
    db_session.add(customer_b)
    db_session.flush()
    ticket_b = _ticket(
        db_session,
        tenant=tenant_b,
        market=market_b,
        team=team_b,
        actor=creator_b,
        customer=customer_b,
        suffix="B",
    )

    with pytest.raises(HTTPException) as exc:
        ensure_ticket_visible(actor, ticket_b, db_session)

    assert exc.value.status_code == 404
    assert exc.value.detail["error_code"] == "tenant_resource_not_found"


def test_ticket_relation_tenant_drift_fails_closed(db_session):
    tenant_a = _tenant(db_session, "tenant-a")
    tenant_b = _tenant(db_session, "tenant-b")
    market_a, team_a, _ = _org(db_session, tenant_a, "A")
    _market_b, _team_b, ownership_b = _org(db_session, tenant_b, "B")
    actor = _user(db_session, tenant_a, team_a, "actor-a")
    customer_b = Customer(name="Wrong Tenant", **ownership_b)
    db_session.add(customer_b)
    db_session.flush()
    ticket = _ticket(
        db_session,
        tenant=tenant_a,
        market=market_a,
        team=team_a,
        actor=actor,
        customer=customer_b,
        suffix="DRIFT",
    )

    with pytest.raises(HTTPException) as exc:
        ensure_ticket_tenant_authority(db_session, actor, ticket)

    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "tenant_resource_conflict"


def test_ticket_and_lite_lists_are_filtered_by_actor_tenant(db_session):
    tenant_a = _tenant(db_session, "tenant-a")
    tenant_b = _tenant(db_session, "tenant-b")
    market_a, team_a, ownership_a = _org(db_session, tenant_a, "A")
    market_b, team_b, ownership_b = _org(db_session, tenant_b, "B")
    actor_a = _user(db_session, tenant_a, team_a, "actor-a")
    actor_b = _user(db_session, tenant_b, team_b, "actor-b")
    customer_a = Customer(name="Customer A", **ownership_a)
    customer_b = Customer(name="Customer B", **ownership_b)
    db_session.add_all([customer_a, customer_b])
    db_session.flush()
    ticket_a = _ticket(
        db_session,
        tenant=tenant_a,
        market=market_a,
        team=team_a,
        actor=actor_a,
        customer=customer_a,
        suffix="A",
    )
    _ticket(
        db_session,
        tenant=tenant_b,
        market=market_b,
        team=team_b,
        actor=actor_b,
        customer=customer_b,
        suffix="B",
    )
    db_session.commit()

    assert [row.id for row in list_tickets(db_session, actor_a)] == [ticket_a.id]
    page = list_lite_cases_page(db_session, actor_a, limit=20)
    assert [row["id"] for row in page["items"]] == [ticket_a.id]


def test_create_ticket_dual_writes_actor_tenant_to_ticket_and_customer(db_session):
    tenant = _tenant(db_session, "tenant-a")
    market, team, _ = _org(db_session, tenant, "A")
    actor = _user(db_session, tenant, team, "creator-a", UserRole.admin)

    ticket = create_ticket(
        db_session,
        TicketCreate(
            title="Tenant ticket",
            description="Tenant create contract",
            source=TicketSource.manual,
            source_channel=SourceChannel.internal,
            priority=TicketPriority.medium,
            market_id=market.id,
            team_id=team.id,
            customer=CustomerInput(name="Created Customer", email="created@invalid.test"),
        ),
        actor,
    )

    assert ticket.tenant_id == tenant.id
    assert ticket.tenant_assignment_source == RUNTIME_TENANT_ASSIGNMENT_SOURCE
    assert ticket.tenant_assignment_version == RUNTIME_TENANT_ASSIGNMENT_VERSION
    assert ticket.customer is not None
    assert ticket.customer.tenant_id == tenant.id
    assert ticket.customer.tenant_assignment_source == RUNTIME_TENANT_ASSIGNMENT_SOURCE


def test_create_ticket_rejects_cross_tenant_customer_team_and_market(db_session):
    tenant_a = _tenant(db_session, "tenant-a")
    tenant_b = _tenant(db_session, "tenant-b")
    market_a, team_a, _ = _org(db_session, tenant_a, "A")
    market_b, team_b, ownership_b = _org(db_session, tenant_b, "B")
    actor = _user(db_session, tenant_a, team_a, "creator-a", UserRole.admin)
    customer_b = Customer(name="Tenant B Customer", **ownership_b)
    db_session.add(customer_b)
    db_session.commit()

    def payload(**overrides):
        values = dict(
            title="Rejected tenant ticket",
            description="Must fail closed",
            source=TicketSource.manual,
            source_channel=SourceChannel.internal,
            priority=TicketPriority.medium,
            market_id=market_a.id,
            team_id=team_a.id,
        )
        values.update(overrides)
        return TicketCreate(**values)

    for candidate in (
        payload(customer_id=customer_b.id),
        payload(team_id=team_b.id),
        payload(market_id=market_b.id),
    ):
        with pytest.raises(HTTPException) as exc:
            create_ticket(db_session, candidate, actor)
        assert exc.value.status_code in {404, 409}
        db_session.rollback()


def test_shadow_legacy_actor_lists_only_unowned_rows_in_mixed_dataset(db_session):
    legacy_actor = _user(db_session, None, None, "legacy-admin", UserRole.admin)
    legacy_ticket = _ticket(
        db_session,
        tenant=None,
        market=None,
        team=None,
        actor=legacy_actor,
        suffix="LEGACY-MIXED",
    )
    tenant = _tenant(db_session, "tenant-owned")
    market, team, ownership = _org(db_session, tenant, "O")
    owner = _user(db_session, tenant, team, "tenant-owner")
    customer = Customer(name="Owned Customer", **ownership)
    db_session.add(customer)
    db_session.flush()
    _ticket(
        db_session,
        tenant=tenant,
        market=market,
        team=team,
        actor=owner,
        customer=customer,
        suffix="OWNED-MIXED",
    )
    db_session.commit()

    assert [row.id for row in list_tickets(db_session, legacy_actor)] == [legacy_ticket.id]
    page = list_lite_cases_page(db_session, legacy_actor, limit=20)
    assert [row["id"] for row in page["items"]] == [legacy_ticket.id]


def test_customer_matching_never_updates_another_tenant(db_session):
    tenant_a = _tenant(db_session, "tenant-a")
    tenant_b = _tenant(db_session, "tenant-b")
    market_a, team_a, _ = _org(db_session, tenant_a, "A")
    _market_b, _team_b, ownership_b = _org(db_session, tenant_b, "B")
    actor = _user(db_session, tenant_a, team_a, "creator-a")
    existing_b = Customer(
        name="Tenant B Original",
        email="shared@invalid.test",
        email_normalized="shared@invalid.test",
        **ownership_b,
    )
    db_session.add(existing_b)
    db_session.commit()

    ticket = create_ticket(
        db_session,
        TicketCreate(
            title="Tenant A duplicate identity",
            description="Same email must remain tenant-scoped",
            source=TicketSource.manual,
            source_channel=SourceChannel.internal,
            priority=TicketPriority.medium,
            market_id=market_a.id,
            team_id=team_a.id,
            customer=CustomerInput(name="Tenant A Customer", email="shared@invalid.test"),
        ),
        actor,
    )

    assert ticket.customer is not None
    assert ticket.customer.id != existing_b.id
    assert ticket.customer.tenant_id == tenant_a.id
    assert db_session.get(Customer, existing_b.id).name == "Tenant B Original"


def test_invalid_runtime_authority_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TENANT_RUNTIME_AUTHORITY_MODE", "permissive")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="TENANT_RUNTIME_AUTHORITY_MODE"):
        get_settings()


def test_actor_with_dangling_team_relation_fails_closed(db_session):
    tenant = _tenant(db_session, "tenant-a")
    actor = _user(db_session, tenant, None, "dangling-team-admin")
    actor.team_id = 999_999
    db_session.flush()

    with pytest.raises(HTTPException) as exc:
        resolve_actor_tenant_id(db_session, actor)

    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "tenant_principal_conflict"


def test_stale_loaded_ticket_relationship_identity_fails_closed(db_session):
    tenant = _tenant(db_session, "tenant-a")
    market, team, ownership = _org(db_session, tenant, "A")
    actor = _user(db_session, tenant, team, "identity-admin")
    first = Customer(name="First", **ownership)
    second = Customer(name="Second", **ownership)
    db_session.add_all([first, second])
    db_session.flush()
    ticket = _ticket(
        db_session,
        tenant=tenant,
        market=market,
        team=team,
        actor=actor,
        customer=first,
        suffix="STALE-RELATION",
    )
    assert ticket.customer is first
    ticket.customer_id = second.id

    with pytest.raises(HTTPException) as exc:
        ensure_ticket_tenant_authority(db_session, actor, ticket)

    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "tenant_resource_conflict"


def test_lite_upsert_cannot_modify_same_tenant_case_outside_role_scope(db_session):
    tenant = _tenant(db_session, "tenant-a")
    market, actor_team, ownership = _org(db_session, tenant, "A")
    other_team = Team(
        name="Other Team A",
        market_id=market.id,
        **ownership,
    )
    db_session.add(other_team)
    db_session.flush()
    actor = _user(db_session, tenant, actor_team, "agent-a", UserRole.agent)
    owner = _user(db_session, tenant, other_team, "agent-b", UserRole.agent)
    customer = Customer(name="Scoped Customer", **ownership)
    db_session.add(customer)
    db_session.flush()
    existing = _ticket(
        db_session,
        tenant=tenant,
        market=market,
        team=other_team,
        actor=owner,
        assignee=owner,
        customer=customer,
        suffix="UPSERT-SCOPE",
    )
    existing.source_chat_id = "chat-upsert-scope"
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        create_lite_case(
            db_session,
            LiteCaseCreate(
                issue_summary="Must not update hidden case",
                customer_request="Tenant is the same but role scope is not",
                source_chat_id="chat-upsert-scope",
                last_customer_message="hidden mutation attempt",
            ),
            actor,
        )

    assert exc.value.status_code == 403
    db_session.refresh(existing)
    assert existing.last_customer_message is None


def test_lite_pagination_validates_lookahead_row_before_has_more(db_session):
    tenant_a = _tenant(db_session, "tenant-a")
    tenant_b = _tenant(db_session, "tenant-b")
    market_a, team_a, ownership_a = _org(db_session, tenant_a, "A")
    _market_b, _team_b, ownership_b = _org(db_session, tenant_b, "B")
    actor = _user(db_session, tenant_a, team_a, "pagination-admin")
    customer_a = Customer(name="Customer A", **ownership_a)
    customer_b = Customer(name="Customer B", **ownership_b)
    db_session.add_all([customer_a, customer_b])
    db_session.flush()
    invalid_lookahead = _ticket(
        db_session,
        tenant=tenant_a,
        market=market_a,
        team=team_a,
        actor=actor,
        customer=customer_b,
        suffix="LOOKAHEAD-INVALID",
    )
    valid_visible = _ticket(
        db_session,
        tenant=tenant_a,
        market=market_a,
        team=team_a,
        actor=actor,
        customer=customer_a,
        suffix="VISIBLE-VALID",
    )
    invalid_lookahead.updated_at = valid_visible.updated_at.replace(year=2020)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        list_lite_cases_page(db_session, actor, limit=1)

    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "tenant_resource_conflict"
