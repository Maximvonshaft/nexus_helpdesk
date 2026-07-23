import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.enums import UserRole
from app.models import User, UserCapabilityOverride
from app.services.permissions import (
    ALL_CAPABILITIES,
    CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
    CAP_SPEEDAF_CANCEL_WRITE,
    CAP_SPEEDAF_VOICE_CALLBACK_WRITE,
    CAP_SPEEDAF_WORK_ORDER_WRITE,
    WEBCALL_VOICE_OPERATOR_CAPABILITIES,
    resolve_capabilities,
    resolve_capabilities_from_preloaded,
)


def _user(role: UserRole) -> User:
    return User(
        username=f"{role.value}-contract",
        display_name="Contract User",
        password_hash="x",
        role=role,
        is_active=True,
    )


def test_tool_and_voice_capabilities_are_in_source_of_truth_catalog():
    expected = {
        CAP_SPEEDAF_WORK_ORDER_WRITE,
        CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
        CAP_SPEEDAF_CANCEL_WRITE,
        CAP_SPEEDAF_VOICE_CALLBACK_WRITE,
        *WEBCALL_VOICE_OPERATOR_CAPABILITIES,
    }

    assert expected.issubset(set(ALL_CAPABILITIES))


def test_operator_roles_get_complete_voice_bundle_but_auditor_does_not():
    for role in (UserRole.admin, UserRole.manager, UserRole.lead, UserRole.agent):
        assert WEBCALL_VOICE_OPERATOR_CAPABILITIES.issubset(
            resolve_capabilities(_user(role))
        )

    assert WEBCALL_VOICE_OPERATOR_CAPABILITIES.isdisjoint(
        resolve_capabilities(_user(UserRole.auditor))
    )


def test_speedaf_write_capabilities_remain_admin_only_by_default():
    speedaf_writes = {
        CAP_SPEEDAF_WORK_ORDER_WRITE,
        CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
        CAP_SPEEDAF_CANCEL_WRITE,
        CAP_SPEEDAF_VOICE_CALLBACK_WRITE,
    }

    assert speedaf_writes.issubset(resolve_capabilities(_user(UserRole.admin)))
    for role in (UserRole.manager, UserRole.lead, UserRole.agent, UserRole.auditor):
        assert speedaf_writes.isdisjoint(resolve_capabilities(_user(role)))


def test_explicit_deny_removes_one_default_voice_capability():
    user = _user(UserRole.agent)
    denied = next(iter(WEBCALL_VOICE_OPERATOR_CAPABILITIES))
    override = UserCapabilityOverride(
        user_id=1,
        capability=denied,
        allowed=False,
    )

    effective = resolve_capabilities_from_preloaded(user, [override])

    assert denied not in effective
    assert not WEBCALL_VOICE_OPERATOR_CAPABILITIES.issubset(effective)


def test_auditor_requires_explicit_allow_for_every_voice_capability():
    user = _user(UserRole.auditor)
    incomplete = [
        UserCapabilityOverride(user_id=1, capability=capability, allowed=True)
        for capability in sorted(WEBCALL_VOICE_OPERATOR_CAPABILITIES)[:-1]
    ]
    complete = [
        UserCapabilityOverride(user_id=1, capability=capability, allowed=True)
        for capability in sorted(WEBCALL_VOICE_OPERATOR_CAPABILITIES)
    ]

    assert not WEBCALL_VOICE_OPERATOR_CAPABILITIES.issubset(
        resolve_capabilities_from_preloaded(user, incomplete)
    )
    assert WEBCALL_VOICE_OPERATOR_CAPABILITIES.issubset(
        resolve_capabilities_from_preloaded(user, complete)
    )
