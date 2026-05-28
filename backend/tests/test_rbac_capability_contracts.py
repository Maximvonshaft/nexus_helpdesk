import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.enums import UserRole
from app.models import User
from app.services.permissions import (
    ALL_CAPABILITIES,
    CAP_AUDIT_READ,
    CAP_SECURITY_READ,
    CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
    CAP_SPEEDAF_CANCEL_WRITE,
    CAP_SPEEDAF_WORK_ORDER_WRITE,
    CAP_WEBCALL_VOICE_ACCEPT,
    CAP_WEBCALL_VOICE_END,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCALL_VOICE_READ,
    CAP_WEBCALL_VOICE_REJECT,
    resolve_capabilities,
)


def _user(role: UserRole) -> User:
    return User(username=f"{role.value}-contract", display_name="Contract User", password_hash="x", role=role, is_active=True)


def test_tool_and_voice_capabilities_are_in_source_of_truth_catalog():
    expected = {
        CAP_SPEEDAF_WORK_ORDER_WRITE,
        CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
        CAP_SPEEDAF_CANCEL_WRITE,
        CAP_SECURITY_READ,
        CAP_AUDIT_READ,
        CAP_WEBCALL_VOICE_READ,
        CAP_WEBCALL_VOICE_QUEUE_VIEW,
        CAP_WEBCALL_VOICE_ACCEPT,
        CAP_WEBCALL_VOICE_REJECT,
        CAP_WEBCALL_VOICE_END,
    }

    assert expected.issubset(set(ALL_CAPABILITIES))


def test_auditor_gets_readonly_security_audit_capabilities_by_default():
    auditor_caps = resolve_capabilities(_user(UserRole.auditor))
    assert CAP_SECURITY_READ in auditor_caps
    assert CAP_AUDIT_READ in auditor_caps


def test_admin_gets_new_high_risk_capabilities_by_default_but_agent_and_auditor_do_not():
    high_risk = {
        CAP_SPEEDAF_WORK_ORDER_WRITE,
        CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
        CAP_SPEEDAF_CANCEL_WRITE,
        CAP_WEBCALL_VOICE_ACCEPT,
        CAP_WEBCALL_VOICE_REJECT,
        CAP_WEBCALL_VOICE_END,
    }

    assert high_risk.issubset(resolve_capabilities(_user(UserRole.admin)))
    assert high_risk.isdisjoint(resolve_capabilities(_user(UserRole.agent)))
    assert high_risk.isdisjoint(resolve_capabilities(_user(UserRole.auditor)))
