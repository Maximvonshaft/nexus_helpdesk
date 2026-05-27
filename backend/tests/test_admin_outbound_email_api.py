from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_outbound_email_api.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.admin_outbound_email import (  # noqa: E402
    create_outbound_email_account,
    disable_outbound_email_account,
    list_outbound_email_accounts,
    update_outbound_email_account,
)
from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import AdminAuditLog, Market, OutboundEmailAccount, User  # noqa: E402
from app.schemas import OutboundEmailAccountCreate, OutboundEmailAccountUpdate  # noqa: E402
from app.services.secret_crypto import SecretCryptoService  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "outbound_email_api.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _user(session, role: UserRole, username: str) -> User:
    row = User(
        username=f"{username}-{_uid()}",
        display_name=username.title(),
        email=f"{username}-{_uid()}@example.test",
        password_hash="x",
        role=role,
        is_active=True,
    )
    session.add(row)
    session.flush()
    return row


def _market(session) -> Market:
    row = Market(code=f"CH{_uid()[:4]}", name=f"Switzerland {_uid()}", country_code="CH", is_active=True)
    session.add(row)
    session.flush()
    return row


def _payload(**overrides) -> OutboundEmailAccountCreate:
    data = {
        "display_name": "Support SMTP",
        "host": "SMTP.EXAMPLE.TEST",
        "port": 587,
        "username": "support@nexusdesk-mail.com",
        "password": "smtp-secret",
        "from_address": "Support@NexusDesk-Mail.com",
        "reply_to": "Replies@NexusDesk-Mail.com",
        "security_mode": "starttls",
        "priority": 10,
    }
    data.update(overrides)
    return OutboundEmailAccountCreate(**data)


def _audit_payloads(session) -> str:
    rows = session.query(AdminAuditLog).order_by(AdminAuditLog.id.asc()).all()
    return "\n".join((row.old_value_json or "") + (row.new_value_json or "") for row in rows)


def test_admin_can_create_list_disable_and_secrets_are_masked(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    market = _market(db_session)

    created = create_outbound_email_account(_payload(market_id=market.id), db=db_session, current_user=admin)

    assert created.host == "smtp.example.test"
    assert created.from_address == "support@nexusdesk-mail.com"
    assert created.reply_to == "replies@nexusdesk-mail.com"
    assert created.password_configured is True
    assert created.password_mask == "********"

    row = db_session.query(OutboundEmailAccount).one()
    assert row.password_encrypted != "smtp-secret"
    assert "smtp-secret" not in row.password_encrypted
    assert SecretCryptoService.outbound_email().decrypt(row.password_encrypted) == "smtp-secret"

    listing = list_outbound_email_accounts(db=db_session, current_user=admin)
    assert [item.id for item in listing] == [created.id]

    disabled = disable_outbound_email_account(created.id, db=db_session, current_user=admin)
    assert disabled.is_active is False

    audit_text = _audit_payloads(db_session)
    assert "smtp-secret" not in audit_text
    assert "password_encrypted" not in audit_text
    assert '"redacted": true' in audit_text.lower()
    assert db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "outbound_email_account.create").count() == 1
    assert db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "outbound_email_account.disable").count() == 1


def test_update_password_rotates_secret_and_writes_redacted_audit(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    created = create_outbound_email_account(_payload(), db=db_session, current_user=admin)

    updated = update_outbound_email_account(
        created.id,
        OutboundEmailAccountUpdate(password="rotated-secret", host="smtp2.example.test"),
        db=db_session,
        current_user=admin,
    )

    assert updated.host == "smtp2.example.test"
    row = db_session.query(OutboundEmailAccount).filter(OutboundEmailAccount.id == created.id).one()
    assert SecretCryptoService.outbound_email().decrypt(row.password_encrypted) == "rotated-secret"
    assert row.health_status == "unknown"
    assert row.last_test_status is None

    audit_text = _audit_payloads(db_session)
    assert "smtp-secret" not in audit_text
    assert "rotated-secret" not in audit_text
    assert db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "outbound_email_account.update").count() == 1
    assert db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "outbound_email_account.password_change").count() == 1


def test_agent_cannot_manage_outbound_email_accounts(db_session):
    agent = _user(db_session, UserRole.agent, "agent")

    with pytest.raises(HTTPException) as exc:
        create_outbound_email_account(_payload(), db=db_session, current_user=agent)

    assert exc.value.status_code == 403


def test_duplicate_route_and_inactive_market_are_rejected(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    create_outbound_email_account(_payload(), db=db_session, current_user=admin)

    with pytest.raises(HTTPException) as duplicate_exc:
        create_outbound_email_account(_payload(password="other-secret"), db=db_session, current_user=admin)
    assert duplicate_exc.value.status_code == 400

    inactive = Market(code="ZZ", name="Inactive", country_code="ZZ", is_active=False)
    db_session.add(inactive)
    db_session.flush()
    with pytest.raises(HTTPException) as market_exc:
        create_outbound_email_account(_payload(host="smtp3.example.test", market_id=inactive.id), db=db_session, current_user=admin)
    assert market_exc.value.status_code == 400


def test_schema_rejects_bad_smtp_values():
    with pytest.raises(ValidationError):
        OutboundEmailAccountCreate(
            host="smtp.example.test",
            port=70000,
            username="support",
            password="secret",
            from_address="support@nexusdesk-mail.com",
            security_mode="starttls",
        )

    with pytest.raises(ValidationError):
        OutboundEmailAccountCreate(
            host="smtp.example.test",
            port=587,
            username="support",
            password="secret",
            from_address="not-an-email",
            security_mode="starttls",
        )


def test_audit_payloads_are_valid_json_when_present(db_session):
    admin = _user(db_session, UserRole.admin, "admin")
    created = create_outbound_email_account(_payload(), db=db_session, current_user=admin)
    update_outbound_email_account(created.id, OutboundEmailAccountUpdate(display_name="Renamed"), db=db_session, current_user=admin)

    for row in db_session.query(AdminAuditLog).all():
        if row.old_value_json:
            json.loads(row.old_value_json)
        if row.new_value_json:
            json.loads(row.new_value_json)
