from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..models import User
from ..models_identity_policy import UserCredentialPolicy
from .permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_AUDIT_READ,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_SECURITY_READ,
    CAP_USER_MANAGE,
    resolve_capabilities,
)

PRIVILEGED_IDENTITY_CAPABILITIES = frozenset(
    {
        CAP_USER_MANAGE,
        CAP_RUNTIME_MANAGE,
        CAP_CHANNEL_ACCOUNT_MANAGE,
        CAP_AI_CONFIG_MANAGE,
        CAP_SECURITY_READ,
        CAP_AUDIT_READ,
    }
)


def _recovery_code_count(policy: UserCredentialPolicy | None) -> int:
    if policy is None or not policy.mfa_recovery_codes_json:
        return 0
    try:
        value = json.loads(policy.mfa_recovery_codes_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    return len(value) if isinstance(value, list) else 0


def collect_privileged_identity_readiness(db: Session) -> dict[str, Any]:
    """Verify every active control-plane identity has a governed credential.

    A password hash cannot prove that the plaintext met policy. Production
    authorization therefore requires a completed product-mediated password
    change, no pending rotation, confirmed MFA, and usable recovery material for
    every identity capable of changing runtime, channels, users, or security
    policy.
    """

    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).all()
    privileged: list[User] = []
    for user in users:
        if resolve_capabilities(user, db) & PRIVILEGED_IDENTITY_CAPABILITIES:
            privileged.append(user)

    reason_codes: list[str] = []
    noncompliant_user_ids: list[int] = []
    details: dict[str, int] = {
        "policy_missing": 0,
        "password_rotation_pending": 0,
        "password_policy_evidence_missing": 0,
        "mfa_not_confirmed": 0,
        "mfa_recovery_unavailable": 0,
    }

    if not privileged:
        reason_codes.append("privileged_identity_missing")

    for user in privileged:
        policy = db.get(UserCredentialPolicy, user.id)
        user_reasons: set[str] = set()
        if policy is None:
            details["policy_missing"] += 1
            user_reasons.add("privileged_identity_policy_missing")
        else:
            if policy.must_change_password:
                details["password_rotation_pending"] += 1
                user_reasons.add("privileged_password_rotation_pending")
            if policy.password_changed_at is None:
                details["password_policy_evidence_missing"] += 1
                user_reasons.add("privileged_password_policy_evidence_missing")
            if not policy.mfa_enabled or policy.mfa_confirmed_at is None:
                details["mfa_not_confirmed"] += 1
                user_reasons.add("privileged_mfa_not_confirmed")
            if _recovery_code_count(policy) < 1:
                details["mfa_recovery_unavailable"] += 1
                user_reasons.add("privileged_mfa_recovery_unavailable")
        if user_reasons:
            noncompliant_user_ids.append(int(user.id))
            reason_codes.extend(sorted(user_reasons))

    return {
        "status": "ready" if not reason_codes else "not_ready",
        "reason_codes": sorted(set(reason_codes)),
        "active_privileged_identities": len(privileged),
        "compliant_privileged_identities": len(privileged) - len(noncompliant_user_ids),
        "noncompliant_user_ids": noncompliant_user_ids,
        "noncompliance_counts": details,
        "required_capabilities": sorted(PRIVILEGED_IDENTITY_CAPABILITIES),
        "contains_secrets": False,
    }


__all__ = [
    "PRIVILEGED_IDENTITY_CAPABILITIES",
    "collect_privileged_identity_readiness",
]
