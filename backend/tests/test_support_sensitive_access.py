from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services import support_sensitive_access as access


def _request(path: str, method: str = "GET") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=7)


def test_non_sensitive_route_is_ignored(monkeypatch):
    db = Mock()
    db.info = {}
    monkeypatch.setattr(access, "resolve_capabilities", lambda user, session: set())

    access.enforce_sensitive_support_request(
        _request("/api/support/conversations"),
        db=db,
        current_user=_user(),
    )


def test_canonical_detail_requires_customer_profile_capability(monkeypatch):
    db = Mock()
    db.info = {}
    monkeypatch.setattr(access, "resolve_capabilities", lambda user, session: {"ticket.read"})

    with pytest.raises(HTTPException) as exc:
        access.enforce_sensitive_support_request(
            _request("/api/support/conversations/detail"),
            db=db,
            current_user=_user(),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "support_sensitive_read_requires_customer_profile_capability"


def test_legacy_thread_requires_same_sensitive_policy(monkeypatch):
    db = Mock()
    db.info = {}
    monkeypatch.setattr(access, "resolve_capabilities", lambda user, session: {"ticket.read"})

    with pytest.raises(HTTPException) as exc:
        access.enforce_sensitive_support_request(
            _request("/api/webchat/admin/tickets/42/thread"),
            db=db,
            current_user=_user(),
        )

    assert exc.value.status_code == 403


def test_authorized_sensitive_read_persists_bounded_audit_once(monkeypatch):
    caller_db = Mock()
    caller_db.info = {}
    audit_db = Mock()
    monkeypatch.setattr(
        access,
        "resolve_capabilities",
        lambda user, session: {"ticket.read", "customer_profile.read"},
    )
    monkeypatch.setattr(access, "SessionLocal", lambda: audit_db)
    log = Mock()
    monkeypatch.setattr(access, "log_admin_audit", log)

    request = _request("/api/webchat/admin/tickets/42/thread")
    access.enforce_sensitive_support_request(
        request,
        db=caller_db,
        current_user=_user(),
    )
    access.enforce_sensitive_support_request(
        request,
        db=caller_db,
        current_user=_user(),
    )

    log.assert_called_once()
    _, kwargs = log.call_args
    assert kwargs["actor_id"] == 7
    assert kwargs["target_id"] == 42
    assert kwargs["new_value"]["pii_payload_logged"] is False
    assert "phone" not in str(kwargs)
    audit_db.commit.assert_called_once()
    audit_db.close.assert_called_once()


def test_sensitive_read_fails_closed_when_audit_is_unavailable(monkeypatch):
    caller_db = Mock()
    caller_db.info = {}
    audit_db = Mock()
    audit_db.commit.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(
        access,
        "resolve_capabilities",
        lambda user, session: {"ticket.read", "customer_profile.read"},
    )
    monkeypatch.setattr(access, "SessionLocal", lambda: audit_db)
    monkeypatch.setattr(access, "log_admin_audit", Mock())

    with pytest.raises(HTTPException) as exc:
        access.enforce_sensitive_support_request(
            _request("/api/support/conversations/detail"),
            db=caller_db,
            current_user=_user(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "support_sensitive_read_audit_unavailable"
    audit_db.rollback.assert_called_once()
    audit_db.close.assert_called_once()
