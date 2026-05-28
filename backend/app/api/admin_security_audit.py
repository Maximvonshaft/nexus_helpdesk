from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..models import AdminAuditLog, User, UserCapabilityOverride
from ..schemas import APIModel
from ..services.permissions import (
    ALL_CAPABILITIES,
    CAP_AUDIT_READ,
    CAP_SECURITY_READ,
    CAP_USER_MANAGE,
    ROLE_CAPABILITIES,
    _base_capabilities,
    ensure_can_read_security_audit,
    resolve_capabilities,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/security-audit", tags=["admin-security-audit"])

SecurityRisk = Literal["low", "medium", "high"]

HIGH_RISK_CAPABILITIES = {
    "ticket.assign",
    "ticket.escalate",
    "ticket.close",
    "outbound.send",
    "note.write.external",
    CAP_USER_MANAGE,
    "channel_account.manage",
    "bulletin.manage",
    "ai_config.manage",
    "runtime.manage",
    "market.manage",
    "tool:speedaf.work_order.create:write",
    "tool:speedaf.order.update_address:write",
    "tool:speedaf.order.cancel:write",
    "webcall.voice.accept",
    "webcall.voice.reject",
    "webcall.voice.end",
    "webchat.handoff.force_takeover",
}

SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "authorization",
    "private_key",
    "api_key",
    "refresh_token",
    "access_token",
    "hash",
)


class SecurityAuditCapabilityRow(APIModel):
    capability: str
    group: str
    risk: SecurityRisk
    roles_allowed: dict[str, bool]


class SecurityAuditRoleRow(APIModel):
    role: str
    capabilities: list[str]
    high_risk_capabilities: list[str]
    capability_count: int
    high_risk_count: int


class SecurityAuditUserLens(APIModel):
    id: int
    username: str
    display_name: str
    email: str | None = None
    role: str
    team_id: int | None = None
    is_active: bool
    capabilities: list[str]
    high_risk_capabilities: list[str]
    override_count: int
    allow_override_count: int
    deny_override_count: int
    risk: SecurityRisk
    last_capability_change_at: datetime | None = None


class SecurityAuditLogEntry(APIModel):
    id: int
    actor_id: int | None = None
    actor_display_name: str | None = None
    action: str
    target_type: str
    target_id: int | None = None
    old_value: dict[str, Any] | list[Any] | str | None = None
    new_value: dict[str, Any] | list[Any] | str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    risk: SecurityRisk
    created_at: datetime


class SecurityAuditSummary(APIModel):
    capability_count: int
    high_risk_capability_count: int
    user_count: int
    active_user_count: int
    admin_user_count: int
    auditor_user_count: int
    high_risk_user_count: int
    override_count: int
    recent_audit_count: int


class SecurityAuditContracts(APIModel):
    readonly: bool
    auditor_readonly: bool
    mutation_api_exposed: bool
    secret_values_exposed: bool
    request_id_available: bool
    required_capabilities: list[str]
    can_manage_users: bool


class SecurityAuditRead(APIModel):
    ok: bool
    summary: SecurityAuditSummary
    contracts: SecurityAuditContracts
    capability_matrix: list[SecurityAuditCapabilityRow]
    role_matrix: list[SecurityAuditRoleRow]
    users: list[SecurityAuditUserLens]
    audit_logs: list[SecurityAuditLogEntry]


def _capability_group(capability: str) -> str:
    if capability.startswith("ticket."):
        return "工单处理"
    if capability.startswith("attachment.") or capability == "customer_profile.read":
        return "附件与客户资料"
    if capability.startswith("outbound.") or capability.startswith("note."):
        return "客户沟通"
    if capability.startswith("ai_"):
        return "AI 辅助"
    if capability in {CAP_USER_MANAGE, CAP_SECURITY_READ, CAP_AUDIT_READ}:
        return "账号权限"
    if capability in {"channel_account.manage", "bulletin.manage", "ai_config.read", "ai_config.manage", "runtime.manage", "market.manage"}:
        return "治理配置"
    if capability.startswith("tool:speedaf."):
        return "Speedaf 工具"
    if capability.startswith("webcall."):
        return "WebCall 语音"
    if capability.startswith("webchat."):
        return "WebChat 接管"
    return "其他权限"


def _risk_for_capability(capability: str) -> SecurityRisk:
    return "high" if capability in HIGH_RISK_CAPABILITIES else "low"


def _risk_for_user(capabilities: list[str], override_count: int) -> SecurityRisk:
    if CAP_USER_MANAGE in capabilities or len([cap for cap in capabilities if cap in HIGH_RISK_CAPABILITIES]) >= 3:
        return "high"
    if override_count > 0 or any(cap in HIGH_RISK_CAPABILITIES for cap in capabilities):
        return "medium"
    return "low"


def _risk_for_audit(action: str, old_value: Any, new_value: Any) -> SecurityRisk:
    normalized = action.lower()
    if "capability" in normalized or "password" in normalized or "secret" in normalized or "disable" in normalized:
        return "high"
    if old_value is not None or new_value is not None:
        return "medium"
    return "low"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _truncate_string(value: str) -> str:
    if len(value) <= 500:
        return value
    return f"{value[:497]}..."


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _is_sensitive_key(str(key)) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _truncate_string(value)
    return value


def _decode_audit_json(raw: str | None) -> dict[str, Any] | list[Any] | str | None:
    if raw is None:
        return None
    try:
        return _redact_value(json.loads(raw))
    except json.JSONDecodeError:
        return _truncate_string(raw)


def _changed_fields(old_value: Any, new_value: Any) -> list[str]:
    if not isinstance(old_value, dict) or not isinstance(new_value, dict):
        return []
    fields = sorted(set(old_value) | set(new_value))
    return [field for field in fields if old_value.get(field) != new_value.get(field)]


def _overrides_by_user(db: Session, user_ids: list[int]) -> dict[int, list[UserCapabilityOverride]]:
    if not user_ids:
        return {}
    rows = (
        db.query(UserCapabilityOverride)
        .filter(UserCapabilityOverride.user_id.in_(user_ids))
        .order_by(UserCapabilityOverride.user_id.asc(), UserCapabilityOverride.capability.asc())
        .all()
    )
    by_user = {user_id: [] for user_id in user_ids}
    for row in rows:
        by_user.setdefault(row.user_id, []).append(row)
    return by_user


def _capabilities_from_overrides(user: User, overrides: list[UserCapabilityOverride]) -> list[str]:
    capabilities = set(_base_capabilities(user.role))
    for override in overrides:
        if override.allowed:
            capabilities.add(override.capability)
        else:
            capabilities.discard(override.capability)
    return sorted(capabilities)


def _serialize_user_lens(user: User, overrides: list[UserCapabilityOverride]) -> SecurityAuditUserLens:
    capabilities = _capabilities_from_overrides(user, overrides)
    high_risk = sorted(cap for cap in capabilities if cap in HIGH_RISK_CAPABILITIES)
    last_change = max((override.updated_at for override in overrides), default=None)
    return SecurityAuditUserLens(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role.value,
        team_id=user.team_id,
        is_active=user.is_active,
        capabilities=capabilities,
        high_risk_capabilities=high_risk,
        override_count=len(overrides),
        allow_override_count=sum(1 for item in overrides if item.allowed),
        deny_override_count=sum(1 for item in overrides if not item.allowed),
        risk=_risk_for_user(capabilities, len(overrides)),
        last_capability_change_at=last_change,
    )


def _capability_matrix() -> list[SecurityAuditCapabilityRow]:
    roles = [UserRole.agent, UserRole.lead, UserRole.manager, UserRole.admin, UserRole.auditor]
    return [
        SecurityAuditCapabilityRow(
            capability=capability,
            group=_capability_group(capability),
            risk=_risk_for_capability(capability),
            roles_allowed={role.value: capability in ROLE_CAPABILITIES.get(role, set()) for role in roles},
        )
        for capability in sorted(ALL_CAPABILITIES)
    ]


def _role_matrix() -> list[SecurityAuditRoleRow]:
    rows: list[SecurityAuditRoleRow] = []
    for role in [UserRole.agent, UserRole.lead, UserRole.manager, UserRole.admin, UserRole.auditor]:
        capabilities = sorted(ROLE_CAPABILITIES.get(role, set()))
        high_risk = sorted(cap for cap in capabilities if cap in HIGH_RISK_CAPABILITIES)
        rows.append(
            SecurityAuditRoleRow(
                role=role.value,
                capabilities=capabilities,
                high_risk_capabilities=high_risk,
                capability_count=len(capabilities),
                high_risk_count=len(high_risk),
            )
        )
    return rows


def _serialize_audit_log(row: AdminAuditLog, actors: dict[int, User]) -> SecurityAuditLogEntry:
    old_value = _decode_audit_json(row.old_value_json)
    new_value = _decode_audit_json(row.new_value_json)
    actor = actors.get(row.actor_id) if row.actor_id is not None else None
    return SecurityAuditLogEntry(
        id=row.id,
        actor_id=row.actor_id,
        actor_display_name=actor.display_name if actor else None,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        old_value=old_value,
        new_value=new_value,
        changed_fields=_changed_fields(old_value, new_value),
        risk=_risk_for_audit(row.action, old_value, new_value),
        created_at=row.created_at,
    )


@router.get("", response_model=SecurityAuditRead)
def get_security_audit(
    limit: int = Query(50, ge=1, le=200),
    action: str | None = Query(None, max_length=120),
    target_type: str | None = Query(None, max_length=80),
    actor_id: int | None = Query(None),
    q: str | None = Query(None, max_length=120),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_read_security_audit(current_user, db)
    current_capabilities = resolve_capabilities(current_user, db)
    users = db.query(User).order_by(User.is_active.desc(), User.role.asc(), User.display_name.asc(), User.id.asc()).all()
    overrides = _overrides_by_user(db, [user.id for user in users])
    user_lens = [_serialize_user_lens(user, overrides.get(user.id, [])) for user in users]

    audit_query = db.query(AdminAuditLog)
    if action:
        audit_query = audit_query.filter(AdminAuditLog.action == action)
    if target_type:
        audit_query = audit_query.filter(AdminAuditLog.target_type == target_type)
    if actor_id is not None:
        audit_query = audit_query.filter(AdminAuditLog.actor_id == actor_id)
    if q:
        needle = f"%{q.strip()}%"
        audit_query = audit_query.filter(
            or_(
                AdminAuditLog.action.ilike(needle),
                AdminAuditLog.target_type.ilike(needle),
                cast(AdminAuditLog.target_id, String).ilike(needle),
            )
        )
    audit_logs = audit_query.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc()).limit(limit).all()
    actor_ids = sorted({row.actor_id for row in audit_logs if row.actor_id is not None})
    actors = {
        actor.id: actor
        for actor in db.query(User).filter(User.id.in_(actor_ids)).all()
    } if actor_ids else {}
    audit_entries = [_serialize_audit_log(row, actors) for row in audit_logs]

    high_risk_users = sum(1 for user in user_lens if user.risk == "high")
    summary = SecurityAuditSummary(
        capability_count=len(ALL_CAPABILITIES),
        high_risk_capability_count=len([cap for cap in ALL_CAPABILITIES if cap in HIGH_RISK_CAPABILITIES]),
        user_count=len(users),
        active_user_count=sum(1 for user in users if user.is_active),
        admin_user_count=sum(1 for user in users if user.role == UserRole.admin),
        auditor_user_count=sum(1 for user in users if user.role == UserRole.auditor),
        high_risk_user_count=high_risk_users,
        override_count=db.query(func.count(UserCapabilityOverride.id)).scalar() or 0,
        recent_audit_count=len(audit_entries),
    )
    return SecurityAuditRead(
        ok=True,
        summary=summary,
        contracts=SecurityAuditContracts(
            readonly=True,
            auditor_readonly=True,
            mutation_api_exposed=False,
            secret_values_exposed=False,
            request_id_available=False,
            required_capabilities=sorted([CAP_SECURITY_READ, CAP_AUDIT_READ]),
            can_manage_users=CAP_USER_MANAGE in current_capabilities,
        ),
        capability_matrix=_capability_matrix(),
        role_matrix=_role_matrix(),
        users=user_lens,
        audit_logs=audit_entries,
    )
