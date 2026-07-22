from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.governance import (
    _find_visible_knowledge_import_duplicate,
    _role_template_assigned_users,
)
from app.db import Base
from app.enums import UserRole
from app.models import Market, Tenant, User
from app.models_control_plane import KnowledgeItem
from app.models_governance import (
    CountryCatalog,
    KnowledgeImportBatch,
    KnowledgeImportDocument,
    MarketCountry,
    MarketGovernanceProfile,
    RoleTemplate,
    RoleTemplateAssignment,
    RoleTemplateVersion,
)
from app.services import governance_service


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'governance_review_regressions.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_knowledge_import_duplicate_detection_is_scoped_to_current_item_visibility(db_session):
    batch = KnowledgeImportBatch(
        tenant_id="tenant-a",
        status="ready",
        total_files=1,
        succeeded_files=1,
        market_id=101,
        channel="webchat",
        audience_scope="customer",
        language="en",
    )
    item = KnowledgeItem(
        item_key="review-scope-item",
        title="Scoped document",
        tenant_id="tenant-a",
        market_id=101,
        channel="webchat",
        audience_scope="customer",
        language="en",
        status="draft",
    )
    db_session.add_all([batch, item])
    db_session.flush()
    document = KnowledgeImportDocument(
        batch_id=batch.id,
        tenant_id="tenant-a",
        position=1,
        original_file_name="policy.pdf",
        sha256="a" * 64,
        status="draft_created",
        knowledge_item_id=item.id,
    )
    db_session.add(document)
    db_session.commit()

    duplicate = _find_visible_knowledge_import_duplicate(
        db_session,
        tenant_id="tenant-a",
        sha256="a" * 64,
        market_id=101,
        channel="webchat",
        audience_scope="customer",
        language="en",
    )
    assert duplicate is not None
    assert duplicate.id == document.id

    mismatched_scopes = (
        {"market_id": 202},
        {"channel": "email"},
        {"audience_scope": "internal"},
        {"language": "de"},
    )
    for override in mismatched_scopes:
        scope = {
            "market_id": 101,
            "channel": "webchat",
            "audience_scope": "customer",
            "language": "en",
            **override,
        }
        assert (
            _find_visible_knowledge_import_duplicate(
                db_session,
                tenant_id="tenant-a",
                sha256="a" * 64,
                **scope,
            )
            is None
        )

    item.channel = "email"
    db_session.commit()
    assert (
        _find_visible_knowledge_import_duplicate(
            db_session,
            tenant_id="tenant-a",
            sha256="a" * 64,
            market_id=101,
            channel="webchat",
            audience_scope="customer",
            language="en",
        )
        is None
    )


def test_role_assignment_uses_the_exact_published_version_snapshot(db_session):
    tenant = Tenant(tenant_key="review-role", display_name="Review Role")
    db_session.add(tenant)
    db_session.flush()
    template = RoleTemplate(
        tenant_id=tenant.id,
        role_key="regional-agent",
        display_name="Regional Agent",
        base_role="admin",
        risk_level="standard",
        draft_capabilities_json=["ticket.read", "user.manage"],
        published_capabilities_json=["ticket.read"],
        published_version=1,
        is_active=True,
    )
    db_session.add(template)
    db_session.flush()
    db_session.add(
        RoleTemplateVersion(
            template_id=template.id,
            version=1,
            snapshot_json={
                "base_role": "agent",
                "capabilities": ["ticket.read"],
                "version": 1,
            },
        )
    )
    db_session.commit()

    base_role, capabilities = governance_service.role_template_version_values(
        db_session, template_id=template.id, version=1
    )
    assert base_role is UserRole.agent
    assert capabilities == ["ticket.read"]


def test_template_assignee_projection_includes_inactive_accounts(db_session):
    tenant = Tenant(tenant_key="review-assignees", display_name="Review Assignees")
    db_session.add(tenant)
    db_session.flush()
    template = RoleTemplate(
        tenant_id=tenant.id,
        role_key="support-agent",
        display_name="Support Agent",
        base_role="agent",
        risk_level="standard",
        draft_capabilities_json=["ticket.read"],
        published_capabilities_json=["ticket.read"],
        published_version=1,
        is_active=True,
    )
    active = User(
        tenant_id=tenant.id,
        username="active-assignee",
        display_name="Active Assignee",
        password_hash="x",
        role=UserRole.agent,
        is_active=True,
    )
    inactive = User(
        tenant_id=tenant.id,
        username="inactive-assignee",
        display_name="Inactive Assignee",
        password_hash="x",
        role=UserRole.admin,
        is_active=False,
    )
    db_session.add_all([template, active, inactive])
    db_session.flush()
    db_session.add_all(
        [
            RoleTemplateAssignment(
                user_id=active.id, template_id=template.id, template_version=1
            ),
            RoleTemplateAssignment(
                user_id=inactive.id, template_id=template.id, template_version=1
            ),
        ]
    )
    db_session.commit()

    assignees = _role_template_assigned_users(
        db_session, tenant_id=tenant.id, template_id=template.id
    )
    assert [user.id for user in assignees] == [active.id, inactive.id]


def test_market_expected_version_is_claimed_atomically_before_mutation(db_session):
    tenant = Tenant(tenant_key="review-market", display_name="Review Market")
    actor = User(
        tenant=tenant,
        username="market-governor",
        display_name="Market Governor",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    market = Market(
        tenant=tenant,
        code="ME-REVIEW",
        name="Original Market",
        country_code="ME",
        language_code="en",
        is_active=True,
    )
    country = CountryCatalog(
        iso_alpha2="ME",
        iso_alpha3="MNE",
        iso_numeric="499",
        canonical_name="Montenegro",
        default_currency="EUR",
        is_available=True,
    )
    db_session.add_all([tenant, actor, market, country])
    db_session.flush()
    profile = MarketGovernanceProfile(
        market_id=market.id,
        status="active",
        version=4,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db_session.add_all(
        [
            profile,
            MarketCountry(
                market_id=market.id, country_code="ME", is_primary=True
            ),
        ]
    )
    db_session.commit()

    governance_service.update_market_governance(
        db_session,
        market=market,
        actor=actor,
        name="Claimed Market",
        expected_version=4,
    )
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(market)
    assert profile.version == 5
    assert market.name == "Claimed Market"

    with pytest.raises(HTTPException) as stale:
        governance_service.update_market_governance(
            db_session,
            market=market,
            actor=actor,
            name="Stale Overwrite",
            expected_version=4,
        )
    assert stale.value.status_code == 409
    db_session.rollback()
    db_session.refresh(profile)
    db_session.refresh(market)
    assert profile.version == 5
    assert market.name == "Claimed Market"
