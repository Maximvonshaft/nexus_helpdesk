from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import UserRole
from ..models import AdminAuditLog, Market, Team, User
from ..operator_models import OperatorQueueScopeGrant
from ..services.permissions import (
    CAP_OPERATOR_QUEUE_READ,
    ensure_capability,
    ensure_can_manage_users,
)
from ..utils.time import utc_now

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_COUNTRY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,15}$")
_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def normalize_operator_scope(
    *,
    tenant_key: str,
    country_code: str,
    channel_key: str,
) -> tuple[str, str, str]:
    tenant = str(tenant_key or "").strip()
    country = str(country_code or "").strip().upper()
    channel = str(channel_key or "").strip().lower()
    if not _TENANT_RE.fullmatch(tenant):
        raise HTTPException(status_code=400, detail="invalid_operator_queue_tenant_scope")
    if not _COUNTRY_RE.fullmatch(country):
        raise HTTPException(status_code=400, detail="invalid_operator_queue_country_scope")
    if not _CHANNEL_RE.fullmatch(channel):
        raise HTTPException(status_code=400, detail="invalid_operator_queue_channel_scope")
    return tenant, country, channel


def tenant_scope_hash(tenant_key: str) -> str:
    return hashlib.sha256(tenant_key.encode("utf-8")).hexdigest()[:12]


def _team_country(db: Session, user) -> str | None:
    if not getattr(user, "team_id", None):
        return None
    row = (
        db.query(Market.country_code)
        .join(Team, Team.market_id == Market.id)
        .filter(Team.id == int(user.team_id), Team.is_active.is_(True), Market.is_active.is_(True))
        .first()
    )
    return str(row[0]).strip().upper() if row and row[0] else None


def active_scope_grant(
    db: Session,
    *,
    user_id: int,
    tenant_key: str,
    country_code: str,
    channel_key: str,
) -> OperatorQueueScopeGrant | None:
    return (
        db.query(OperatorQueueScopeGrant)
        .filter(
            OperatorQueueScopeGrant.user_id == user_id,
            OperatorQueueScopeGrant.tenant_key == tenant_key,
            OperatorQueueScopeGrant.country_code == country_code,
            OperatorQueueScopeGrant.channel_key == channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )


def authorize_operator_scope(
    db: Session,
    *,
    current_user,
    tenant_key: str,
    country_code: str,
    channel_key: str,
) -> tuple[str, str, str, OperatorQueueScopeGrant | None]:
    ensure_capability(
        current_user,
        CAP_OPERATOR_QUEUE_READ,
        db,
        message="Operator queue read permission required",
    )
    tenant, country, channel = normalize_operator_scope(
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
    )
    if current_user.role == UserRole.admin:
        return tenant, country, channel, None

    if current_user.role in {UserRole.agent, UserRole.lead}:
        team_country = _team_country(db, current_user)
        if team_country is None or team_country != country:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="operator_queue_team_scope_mismatch",
            )

    grant = active_scope_grant(
        db,
        user_id=int(current_user.id),
        tenant_key=tenant,
        country_code=country,
        channel_key=channel,
    )
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_queue_scope_not_granted",
        )
    return tenant, country, channel, grant


def scope_grant_version(grant: OperatorQueueScopeGrant | None, *, current_user) -> str:
    auth_context = (
        f"role:{getattr(getattr(current_user, 'role', None), 'value', getattr(current_user, 'role', 'unknown'))}:"
        f"team:{getattr(current_user, 'team_id', None) or 'none'}"
    )
    if grant is None:
        raw = f"admin:{int(current_user.id)}:{auth_context}"
    else:
        updated = grant.updated_at.isoformat() if isinstance(grant.updated_at, datetime) else str(grant.updated_at)
        raw = f"grant:{grant.id}:{updated}:{int(bool(grant.enabled))}:{auth_context}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def serialize_scope_grant(row: OperatorQueueScopeGrant) -> dict[str, object]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "tenant_hash": tenant_scope_hash(row.tenant_key),
        "country_code": row.country_code,
        "channel_key": row.channel_key,
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def serialize_current_scope_grant(row: OperatorQueueScopeGrant) -> dict[str, str]:
    """Return the exact scope only to the user who already owns the active grant."""
    return {
        "tenant_key": row.tenant_key,
        "tenant_hash": tenant_scope_hash(row.tenant_key),
        "country_code": row.country_code,
        "channel_key": row.channel_key,
    }


def list_current_scope_grants(db: Session, *, current_user) -> dict[str, object]:
    """Project existing queue grants into a safe current-user workspace selector.

    This is not a second authorization source. Every returned tuple is still
    validated by ``authorize_operator_scope`` when the queue is read.
    """
    ensure_capability(
        current_user,
        CAP_OPERATOR_QUEUE_READ,
        db,
        message="Operator queue read permission required",
    )
    query = db.query(OperatorQueueScopeGrant).filter(
        OperatorQueueScopeGrant.user_id == int(current_user.id),
        OperatorQueueScopeGrant.enabled.is_(True),
    )
    if current_user.role in {UserRole.agent, UserRole.lead}:
        team_country = _team_country(db, current_user)
        if team_country is None:
            rows: list[OperatorQueueScopeGrant] = []
        else:
            rows = query.filter(OperatorQueueScopeGrant.country_code == team_country).order_by(
                OperatorQueueScopeGrant.country_code.asc(),
                OperatorQueueScopeGrant.channel_key.asc(),
                OperatorQueueScopeGrant.tenant_key.asc(),
            ).all()
    else:
        rows = query.order_by(
            OperatorQueueScopeGrant.country_code.asc(),
            OperatorQueueScopeGrant.channel_key.asc(),
            OperatorQueueScopeGrant.tenant_key.asc(),
        ).all()
    return {
        "items": [serialize_current_scope_grant(row) for row in rows],
        "requires_explicit_admin_scope": current_user.role == UserRole.admin and not rows,
    }


def _audit(
    db: Session,
    *,
    actor_id: int,
    action: str,
    row: OperatorQueueScopeGrant,
    old_enabled: bool | None,
) -> None:
    safe_scope = {
        "tenant_hash": tenant_scope_hash(row.tenant_key),
        "country_code": row.country_code,
        "channel_key": row.channel_key,
        "enabled": bool(row.enabled),
    }
    db.add(
        AdminAuditLog(
            actor_id=actor_id,
            action=action,
            target_type="operator_queue_scope_grant",
            target_id=row.id,
            old_value_json=json.dumps({"enabled": old_enabled}) if old_enabled is not None else None,
            new_value_json=json.dumps(safe_scope, sort_keys=True, separators=(",", ":")),
            created_at=utc_now(),
        )
    )


def upsert_scope_grant(db: Session, *, current_user, payload) -> OperatorQueueScopeGrant:
    ensure_can_manage_users(current_user, db)
    tenant, country, channel = normalize_operator_scope(
        tenant_key=payload.tenant_key,
        country_code=payload.country_code,
        channel_key=payload.channel_key,
    )
    target = db.query(User).filter(User.id == payload.user_id, User.is_active.is_(True)).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Operator queue grant user not found")
    row = (
        db.query(OperatorQueueScopeGrant)
        .filter(
            OperatorQueueScopeGrant.user_id == payload.user_id,
            OperatorQueueScopeGrant.tenant_key == tenant,
            OperatorQueueScopeGrant.country_code == country,
            OperatorQueueScopeGrant.channel_key == channel,
        )
        .first()
    )
    old_enabled: bool | None = None
    if row is None:
        row = OperatorQueueScopeGrant(
            user_id=payload.user_id,
            tenant_key=tenant,
            country_code=country,
            channel_key=channel,
            enabled=bool(payload.enabled),
            granted_by=current_user.id,
        )
        db.add(row)
        db.flush()
        action = "operator_queue.scope_grant.created"
    else:
        old_enabled = bool(row.enabled)
        row.enabled = bool(payload.enabled)
        row.granted_by = current_user.id
        row.updated_at = utc_now()
        db.flush()
        action = "operator_queue.scope_grant.updated"
    _audit(db, actor_id=current_user.id, action=action, row=row, old_enabled=old_enabled)
    db.flush()
    return row


def delete_scope_grant(db: Session, *, current_user, grant_id: int) -> None:
    ensure_can_manage_users(current_user, db)
    row = db.query(OperatorQueueScopeGrant).filter(OperatorQueueScopeGrant.id == grant_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Operator queue scope grant not found")
    _audit(
        db,
        actor_id=current_user.id,
        action="operator_queue.scope_grant.deleted",
        row=row,
        old_enabled=bool(row.enabled),
    )
    db.delete(row)
    db.flush()
