from types import SimpleNamespace

from app.enums import UserRole
from app.services.scope_permissions import has_global_admin_visibility, has_global_case_visibility


def _user(role: UserRole):
    return SimpleNamespace(id=1, role=role)


def test_case_visibility_is_capability_derived():
    assert has_global_case_visibility(_user(UserRole.admin)) is True
    assert has_global_case_visibility(_user(UserRole.manager)) is True
    assert has_global_case_visibility(_user(UserRole.auditor)) is True
    assert has_global_case_visibility(_user(UserRole.agent)) is False


def test_admin_visibility_is_narrower_than_case_assignment():
    assert has_global_admin_visibility(_user(UserRole.admin)) is True
    assert has_global_admin_visibility(_user(UserRole.auditor)) is True
    assert has_global_admin_visibility(_user(UserRole.manager)) is False
    assert has_global_admin_visibility(_user(UserRole.agent)) is False
