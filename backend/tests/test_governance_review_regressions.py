from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import inspect
from unittest.mock import MagicMock
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api.governance as governance_api
from app.api.governance import (
    _find_visible_knowledge_import_duplicate,
    _role_template_assigned_users,
)
from app.db import Base
from app.enums import UserRole
from app.models import Market, Tenant, User
from app.models_agent_control import AgentDefinition, AgentRelease
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
from app.services.agent_release_service import activate_deployment


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


def test_initial_market_version_zero_is_claimed_on_first_governance_edit(db_session):
    tenant = Tenant(tenant_key="initial-market", display_name="Initial Market")
    actor = User(
        tenant=tenant,
        username="initial-market-admin",
        display_name="Initial Market Admin",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    market = Market(
        tenant=tenant,
        code="INITIAL-ME",
        name="Initial Name",
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
    db_session.add(MarketCountry(market_id=market.id, country_code="ME", is_primary=True))
    db_session.commit()

    profile = governance_service.update_market_governance(
        db_session,
        market=market,
        actor=actor,
        name="First Governed Name",
        expected_version=0,
    )
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(market)
    assert profile.version == 1
    assert market.name == "First Governed Name"


def test_market_name_conflict_matches_database_wide_unique_constraint(db_session):
    tenant_a = Tenant(tenant_key="market-name-a", display_name="Market Name A")
    tenant_b = Tenant(tenant_key="market-name-b", display_name="Market Name B")
    actor = User(
        tenant=tenant_a,
        username="market-name-admin",
        display_name="Market Name Admin",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    market_a = Market(
        tenant=tenant_a,
        code="NAME-A",
        name="Tenant A Market",
        country_code="ME",
        language_code="en",
        is_active=True,
    )
    market_b = Market(
        tenant=tenant_b,
        code="NAME-B",
        name="Reserved Global Name",
        country_code="DE",
        language_code="de",
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
    db_session.add_all([tenant_a, tenant_b, actor, market_a, market_b, country])
    db_session.flush()
    db_session.add(MarketCountry(market_id=market_a.id, country_code="ME", is_primary=True))
    db_session.commit()

    with pytest.raises(HTTPException) as conflict:
        governance_service.update_market_governance(
            db_session,
            market=market_a,
            actor=actor,
            name="Reserved Global Name",
            expected_version=0,
        )
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "market_name_exists"
    db_session.rollback()


def test_pause_trial_preserves_candidate_release_with_zero_traffic(monkeypatch):
    deployment = SimpleNamespace(active_release_id=11, canary_release_id=22)
    active = SimpleNamespace(id=11)
    candidate = SimpleNamespace(id=22)
    captured = {}

    monkeypatch.setattr(governance_api, "ensure_can_manage_runtime", lambda *_: None)
    monkeypatch.setattr(governance_api, "managed_session", lambda *_: nullcontext())
    monkeypatch.setattr(
        governance_api, "_deployment_for_actor", lambda *_: ("tenant", deployment)
    )
    monkeypatch.setattr(
        governance_api,
        "_release_or_404",
        lambda _db, release_id: active if release_id == 11 else candidate,
    )
    monkeypatch.setattr(governance_api, "_deployment_snapshot", lambda *_: {})

    def apply_state(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(revision=7)

    monkeypatch.setattr(governance_api, "_apply_canary_state", apply_state)

    result = governance_api.pause_trial(
        1,
        governance_api.CanaryActionRequest(reason="pause safely"),
        db=object(),
        current_user=SimpleNamespace(id=9),
    )
    assert result["ok"] is True
    assert captured["active"] is active
    assert captured["canary"] is candidate
    assert captured["percent"] == 0
    assert captured["action"] == "canary_pause"


def test_activate_deployment_allows_a_paused_zero_percent_canary(db_session):
    definition = AgentDefinition(
        tenant_key="paused-canary",
        definition_key="paused-canary-agent",
        name="Paused Canary Agent",
        draft_manifest_json={},
    )
    db_session.add(definition)
    db_session.flush()
    stable = AgentRelease(
        definition_id=definition.id,
        version=1,
        status="approved",
        manifest_json={},
        manifest_sha256="a" * 64,
        validation_json={},
    )
    candidate = AgentRelease(
        definition_id=definition.id,
        version=2,
        status="approved",
        manifest_json={},
        manifest_sha256="b" * 64,
        validation_json={},
    )
    db_session.add_all([stable, candidate])
    db_session.flush()

    deployment = activate_deployment(
        db_session,
        tenant_key="paused-canary",
        environment="staging",
        release=stable,
        canary_release=candidate,
        canary_percent=0,
        actor_id=None,
    )
    assert deployment.active_release_id == stable.id
    assert deployment.canary_release_id == candidate.id
    assert deployment.canary_percent == 0

    with pytest.raises(HTTPException) as invalid:
        activate_deployment(
            db_session,
            tenant_key="paused-canary",
            environment="production",
            release=stable,
            canary_release=None,
            canary_percent=1,
            actor_id=None,
        )
    assert invalid.value.status_code == 400
    assert invalid.value.detail == "agent_canary_release_and_percent_must_match"


def test_seeded_role_capabilities_decode_cross_database_json_arrays():
    root = Path(__file__).resolve().parents[1]
    migration_path = (
        root / "alembic" / "versions" / "20260721_0073_governed_operator_configuration.py"
    )
    spec = importlib.util.spec_from_file_location(
        "governed_operator_configuration_migration", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration._as_json_array(["ticket.read"]) == ["ticket.read"]
    assert migration._as_json_array(
        '["ticket.read", "user.manage"]'
    ) == ["ticket.read", "user.manage"]
    with pytest.raises(RuntimeError, match="role_template_capabilities_json_invalid"):
        migration._as_json_array('"ticket.read"')


def test_role_description_distinguishes_omitted_from_explicit_null(db_session):
    tenant = Tenant(tenant_key="description-tenant", display_name="Description Tenant")
    db_session.add(tenant)
    db_session.flush()
    actor = User(
        tenant_id=tenant.id,
        username="description-actor",
        display_name="Description Actor",
        password_hash="test",
        role=UserRole.admin,
        is_active=True,
    )
    template = RoleTemplate(
        tenant_id=tenant.id,
        role_key="description-semantics",
        display_name="Description semantics",
        description="old description",
        base_role=UserRole.agent.value,
        risk_level="standard",
        draft_capabilities_json=["ticket.read"],
    )
    db_session.add_all([actor, template])
    db_session.commit()

    governance_service.update_role_template(
        db_session, row=template, actor=actor, display_name="Renamed"
    )
    assert template.description == "old description"

    governance_service.update_role_template(
        db_session, row=template, actor=actor, description=None
    )
    assert template.description is None


def test_governance_access_mutations_lock_scope_inside_transaction():
    db = MagicMock()
    tenant = Tenant(tenant_key="governance-lock", display_name="Governance Lock")
    tenant.id = 91
    tenant.is_active = True
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.one_or_none.return_value = tenant

    governance_api._lock_governance_scope(db, tenant.id)
    query.with_for_update.assert_called_once_with()

    for endpoint in (
        governance_api.publish_role_template,
        governance_api.apply_role_template,
    ):
        source = inspect.getsource(endpoint)
        assert source.index("with managed_session(db):") < source.index(
            "_lock_governance_scope"
        )
        assert source.index("_lock_governance_scope") < source.index(
            "_ensure_governance_access_survives"
        )
