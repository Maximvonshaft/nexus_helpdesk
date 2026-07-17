from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.services import support_sensitive_access as access


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=7)


def test_sensitive_capability_uses_canonical_permission_authority(monkeypatch):
    db = Mock()
    ensure = Mock()
    monkeypatch.setattr(access, "ensure_capability", ensure)

    access.ensure_sensitive_support_capability(db, _user())

    ensure.assert_called_once_with(
        _user(),
        "customer_profile.read",
        db,
        message="support_sensitive_read_requires_customer_profile_capability",
    )


def test_authorized_read_persists_bounded_post_scope_evidence(monkeypatch):
    audit_db = Mock()
    monkeypatch.setattr(access, "SessionLocal", lambda: audit_db)
    log = Mock()
    monkeypatch.setattr(access, "log_admin_audit", log)

    access.audit_sensitive_support_read(
        current_user=_user(),
        ticket_id=42,
        includes_support_memory=True,
    )

    log.assert_called_once()
    _, kwargs = log.call_args
    assert kwargs == {
        "actor_id": 7,
        "action": "support_sensitive_read_authorized",
        "target_type": "support_conversation",
        "target_id": 42,
        "new_value": {
            "surface": "webchat_thread",
            "method": "GET",
            "capability": "customer_profile.read",
            "authorization_stage": "object_scope_completed",
            "access_outcome": "authorized",
            "includes_support_memory": True,
            "pii_payload_logged": False,
        },
    }
    assert "phone" not in str(kwargs)
    assert "email" not in str(kwargs)
    audit_db.commit.assert_called_once()
    audit_db.close.assert_called_once()


def test_invalid_target_never_creates_audit_session(monkeypatch):
    session_factory = Mock()
    monkeypatch.setattr(access, "SessionLocal", session_factory)

    with pytest.raises(ValueError, match="invalid_sensitive_support_target"):
        access.audit_sensitive_support_read(
            current_user=_user(),
            ticket_id=0,
            includes_support_memory=False,
        )

    session_factory.assert_not_called()


def test_sensitive_read_fails_closed_when_audit_is_unavailable(monkeypatch):
    audit_db = Mock()
    audit_db.commit.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(access, "SessionLocal", lambda: audit_db)
    monkeypatch.setattr(access, "log_admin_audit", Mock())

    with pytest.raises(HTTPException) as exc:
        access.audit_sensitive_support_read(
            current_user=_user(),
            ticket_id=42,
            includes_support_memory=False,
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "support_sensitive_read_audit_unavailable"
    audit_db.rollback.assert_called_once()
    audit_db.close.assert_called_once()
