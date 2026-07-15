from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.enums import UserRole
from app.services.runtime_permissions import ensure_can_manage_runtime, ensure_can_read_runtime


def _user(role: UserRole):
    return SimpleNamespace(id=1, role=role)


def test_auditor_can_read_runtime_without_manage_authority():
    ensure_can_read_runtime(_user(UserRole.auditor))
    with pytest.raises(HTTPException) as exc:
        ensure_can_manage_runtime(_user(UserRole.auditor))
    assert exc.value.status_code == 403


def test_admin_can_read_and_manage_runtime():
    user = _user(UserRole.admin)
    ensure_can_read_runtime(user)
    ensure_can_manage_runtime(user)


def test_agent_cannot_read_runtime_without_explicit_override():
    with pytest.raises(HTTPException) as exc:
        ensure_can_read_runtime(_user(UserRole.agent))
    assert exc.value.status_code == 403
    assert exc.value.detail == "Not authorized to read runtime"
