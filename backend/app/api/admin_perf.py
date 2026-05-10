from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User, UserCapabilityOverride
from ..services.permissions import ALL_CAPABILITIES, _base_capabilities, ensure_can_manage_users
from .deps import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])

DEFAULT_ADMIN_USERS_LIMIT = 50
MAX_ADMIN_USERS_LIMIT = 100


def _safe_limit(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_ADMIN_USERS_LIMIT), MAX_ADMIN_USERS_LIMIT))


def _role_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _serialize_dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _capabilities_from_preloaded(user: User, overrides: list[UserCapabilityOverride]) -> list[str]:
    capabilities = set(_base_capabilities(user.role))
    for override in overrides:
        if override.allowed:
            capabilities.add(override.capability)
        else:
            capabilities.discard(override.capability)
    return sorted(cap for cap in capabilities if cap in ALL_CAPABILITIES)


def _serialize_user_preloaded(user: User, overrides_by_user: dict[int, list[UserCapabilityOverride]]) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": _role_value(user.role),
        "team_id": user.team_id,
        "is_active": user.is_active,
        "capabilities": _capabilities_from_preloaded(user, overrides_by_user.get(user.id, [])),
        "created_at": _serialize_dt(user.created_at),
        "updated_at": _serialize_dt(user.updated_at),
    }


@router.get("/users")
def list_admin_users_paginated(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    limit: int = Query(DEFAULT_ADMIN_USERS_LIMIT, ge=1),
    cursor: int | None = Query(default=None, ge=0),
    include_inactive: bool = False,
    legacy: bool = False,
):
    ensure_can_manage_users(current_user, db)
    safe_limit = _safe_limit(limit)

    if cursor is not None and cursor < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor")

    query = db.query(User)
    if not include_inactive and not legacy:
        query = query.filter(User.is_active.is_(True))
    if cursor is not None:
        query = query.filter(User.id > cursor)

    rows = query.order_by(User.id.asc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]
    user_ids = [row.id for row in visible]
    overrides_by_user: dict[int, list[UserCapabilityOverride]] = {user_id: [] for user_id in user_ids}
    if user_ids:
        override_rows = (
            db.query(UserCapabilityOverride)
            .filter(UserCapabilityOverride.user_id.in_(user_ids))
            .order_by(UserCapabilityOverride.user_id.asc(), UserCapabilityOverride.capability.asc())
            .all()
        )
        for override in override_rows:
            overrides_by_user.setdefault(override.user_id, []).append(override)

    items = [_serialize_user_preloaded(row, overrides_by_user) for row in visible]
    if legacy:
        return items

    next_cursor = str(visible[-1].id) if len(rows) > safe_limit and visible else None
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
        "filters": {
            "limit": safe_limit,
            "include_inactive": include_inactive,
        },
    }
