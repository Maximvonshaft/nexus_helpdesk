import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.enums import UserRole
from app.models import User
from app.services.permissions import (
    ALL_CAPABILITIES,
    CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
    CAP_SPEEDAF_CANCEL_WRITE,
    CAP_SPEEDAF_WORK_ORDER_WRITE,
    CAP_WEBCALL_VOICE_ACCEPT,
    CAP_WEBCALL_VOICE_END,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCALL_VOICE_READ,
    CAP_WEBCALL_VOICE_REJECT,
    CAP_WEBCHAT_REALTIME_MONITOR,
    resolve_capabilities,
)


def _user(role: UserRole) -> User:
    return User(username=f"{role.value}-contract", display_name="Contract User", password_hash="x", role=role, is_active=True)


def test_tool_and_voice_capabilities_are_in_source_of_truth_catalog():
    expected = {
        CAP_SPEEDAF_WORK_ORDER_WRITE,
        CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
        CAP_SPEEDAF_CANCEL_WRITE,
        CAP_WEBCALL_VOICE_READ,
        CAP_WEBCALL_VOICE_QUEUE_VIEW,
        CAP_WEBCALL_VOICE_ACCEPT,
        CAP_WEBCALL_VOICE_REJECT,
        CAP_WEBCALL_VOICE_END,
        CAP_WEBCHAT_REALTIME_MONITOR,
    }

    assert expected.issubset(set(ALL_CAPABILITIES))


def test_realtime_monitor_is_read_only_and_not_agent_default():
    assert CAP_WEBCHAT_REALTIME_MONITOR in resolve_capabilities(_user(UserRole.admin))
    assert CAP_WEBCHAT_REALTIME_MONITOR in resolve_capabilities(_user(UserRole.manager))
    assert CAP_WEBCHAT_REALTIME_MONITOR in resolve_capabilities(_user(UserRole.lead))
    assert CAP_WEBCHAT_REALTIME_MONITOR in resolve_capabilities(_user(UserRole.auditor))
    assert CAP_WEBCHAT_REALTIME_MONITOR not in resolve_capabilities(_user(UserRole.agent))


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
