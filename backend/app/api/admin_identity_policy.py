from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..models import User
from ..services.credential_creation_context import administrator_issued_tenant_scope
from ..services.identity_tenant_scope import (
    active_team_for_actor,
    actor_tenant_id,
    apply_tenant_scope,
    user_for_actor,
)
from ..services.permissions import CAP_USER_MANAGE, ensure_can_manage_users
from .deps import get_current_user

_USER_COLLECTION_PATH = "/api/admin/users"
_USER_TARGET_PATH = re.compile(r"^/api/admin/users/(?P<user_id>\d+)(?:/.*)?$")


def _is_user_command(request: Request) -> bool:
    return request.url.path == _USER_COLLECTION_PATH or bool(_USER_TARGET_PATH.fullmatch(request.url.path))


async def _payload(request: Request) -> dict[str, Any]:
    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return {}
    try:
        value = await request.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _last_active_admin_in_scope(db: Session, tenant_id: int | None, target: User) -> bool:
    if target.role != UserRole.admin or not target.is_active:
        return False
    query = db.query(User).filter(User.role == UserRole.admin, User.is_active.is_(True))
    return apply_tenant_scope(query, User, tenant_id).count() == 1


async def enforce_admin_identity_request_policy(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AsyncIterator[None]:
    """Protect the existing `/api/admin/users` authority without duplicating it.

    The dependency resolves the administrator Tenant once, validates every
    referenced User and Team against it, protects the final tenant administrator
    from losing governance capability, and exposes the server-derived Tenant to
    the existing User constructor through a request-scoped context.
    """

    if not _is_user_command(request):
        yield
        return

    ensure_can_manage_users(current_user, db)
    tenant_id = actor_tenant_id(db, current_user)
    payload = await _payload(request)
    target: User | None = None

    match = _USER_TARGET_PATH.fullmatch(request.url.path)
    if match is not None:
        target = user_for_actor(db, tenant_id, int(match.group("user_id")))
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    team_id = payload.get("team_id")
    if team_id is not None:
        try:
            normalized_team_id = int(team_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid team_id") from exc
        if active_team_for_actor(db, tenant_id, normalized_team_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team not found or inactive")

    if target is not None and request.method.upper() == "PATCH" and _last_active_admin_in_scope(db, tenant_id, target):
        requested_role = payload.get("role")
        if requested_role is not None and requested_role != UserRole.admin.value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot demote the final active administrator")
        requested_capabilities = payload.get("capabilities")
        if isinstance(requested_capabilities, list) and CAP_USER_MANAGE not in requested_capabilities:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The final active administrator must retain user.manage")

    issued_tenant_id = tenant_id if request.method.upper() == "POST" and request.url.path == _USER_COLLECTION_PATH else None
    with administrator_issued_tenant_scope(issued_tenant_id):
        yield
