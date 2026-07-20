from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "outbound-email-tenant-routing-secret-long-enough")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.models import Market, OutboundEmailAccount, Tenant  # noqa: E402
from app.services.outbound_email_account_service import (  # noqa: E402
    has_active_outbound_email_account,
    resolve_outbound_email_account,
)


def _account(db, *, suffix: str, market_id: int | None, priority: int = 100):
    row = OutboundEmailAccount(
        display_name=f"Route {suffix}",
        host=f"smtp-{suffix}.example.test",
        port=587,
        username=f"user-{suffix}",
        password_encrypted="encrypted-placeholder",
        from_address=f"route-{suffix}@example.test",
        security_mode="starttls",
        market_id=market_id,
        is_active=True,
        priority=priority,
        health_status="ok",
    )
    db.add(row)
    db.flush()
    return row


def test_tenant_market_never_falls_back_to_legacy_global_account(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'email-routing.db'}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()
    try:
        tenant = Tenant(tenant_key="email-tenant", display_name="Email Tenant", is_active=True)
        db.add(tenant)
        db.flush()
        tenant_market = Market(
            tenant_id=tenant.id,
            code="ET",
            name="Email Tenant Market",
            country_code="ET",
            is_active=True,
        )
        legacy_market = Market(
            tenant_id=None,
            code="LG",
            name="Legacy Market",
            country_code="LG",
            is_active=True,
        )
        db.add_all([tenant_market, legacy_market])
        db.flush()

        global_account = _account(db, suffix="global", market_id=None, priority=1)
        db.commit()

        assert resolve_outbound_email_account(db, market_id=tenant_market.id) is None
        assert resolve_outbound_email_account(db, market_id=legacy_market.id).id == global_account.id
        assert has_active_outbound_email_account(
            db,
            ticket=SimpleNamespace(tenant_id=tenant.id, market_id=None),
        ) is False

        tenant_account = _account(db, suffix="tenant", market_id=tenant_market.id, priority=50)
        db.commit()
        resolved = resolve_outbound_email_account(db, market_id=tenant_market.id)
        assert resolved is not None
        assert resolved.id == tenant_account.id
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
