from types import SimpleNamespace

import pytest

from app.enums import UserRole
from app.services.permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_MARKET_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_USER_MANAGE,
    ALL_CAPABILITIES,
    resolve_capabilities,
)


def test_manager_default_system_governance_capabilities_are_removed():
    user = SimpleNamespace(id=1, role=UserRole.manager)
    caps = resolve_capabilities(user)
    assert CAP_USER_MANAGE not in caps
    assert CAP_CHANNEL_ACCOUNT_MANAGE not in caps
    assert CAP_AI_CONFIG_MANAGE not in caps
    assert CAP_RUNTIME_MANAGE not in caps
    assert CAP_MARKET_MANAGE not in caps


def test_admin_still_has_all_capabilities():
    user = SimpleNamespace(id=1, role=UserRole.admin)
    assert resolve_capabilities(user) == set(ALL_CAPABILITIES)
