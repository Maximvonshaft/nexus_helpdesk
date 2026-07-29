from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/r15-tenant-reference.db")

from app.db import Base
from app.model_registry import register_all_models
from app.models import Market, Team, Tenant

register_all_models()


def test_two_tenants_can_use_same_team_and_market_identity(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tenant-reference.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        alpha = Tenant(tenant_key="r15-alpha", display_name="Alpha", is_active=True)
        bravo = Tenant(tenant_key="r15-bravo", display_name="Bravo", is_active=True)
        db.add_all([alpha, bravo])
        db.flush()
        alpha_market = Market(
            tenant_id=alpha.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            code="CH",
            name="Switzerland",
            country_code="CH",
            is_active=True,
        )
        bravo_market = Market(
            tenant_id=bravo.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            code="CH",
            name="Switzerland",
            country_code="CH",
            is_active=True,
        )
        db.add_all([alpha_market, bravo_market])
        db.flush()
        db.add_all(
            [
                Team(
                    tenant_id=alpha.id,
                    tenant_assignment_source="test",
                    tenant_assignment_version="r15",
                    name="Support",
                    team_type="support",
                    market_id=alpha_market.id,
                    is_active=True,
                ),
                Team(
                    tenant_id=bravo.id,
                    tenant_assignment_source="test",
                    tenant_assignment_version="r15",
                    name="Support",
                    team_type="support",
                    market_id=bravo_market.id,
                    is_active=True,
                ),
            ]
        )
        db.commit()
        assert db.query(Market).count() == 2
        assert db.query(Team).count() == 2

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_same_tenant_case_insensitive_duplicates_fail_atomically(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tenant-duplicate.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        tenant = Tenant(tenant_key="r15-duplicate", display_name="Duplicate", is_active=True)
        db.add(tenant)
        db.flush()
        market = Market(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            code="CH",
            name="Switzerland",
            country_code="CH",
            is_active=True,
        )
        db.add(market)
        db.flush()
        db.add(
            Market(
                tenant_id=tenant.id,
                tenant_assignment_source="test",
                tenant_assignment_version="r15",
                code="ch",
                name="Other Name",
                country_code="CH",
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        tenant = db.query(Tenant).filter(Tenant.tenant_key == "r15-duplicate").one()
        market = db.query(Market).filter(Market.tenant_id == tenant.id).one()
        db.add(
            Team(
                tenant_id=tenant.id,
                tenant_assignment_source="test",
                tenant_assignment_version="r15",
                name="Support",
                team_type="support",
                market_id=market.id,
                is_active=True,
            )
        )
        db.flush()
        db.add(
            Team(
                tenant_id=tenant.id,
                tenant_assignment_source="test",
                tenant_assignment_version="r15",
                name="support",
                team_type="support",
                market_id=market.id,
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()

    Base.metadata.drop_all(engine)
    engine.dispose()
