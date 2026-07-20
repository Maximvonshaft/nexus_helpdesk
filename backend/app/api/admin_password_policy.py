from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request, status

from ..services.credential_creation_context import administrator_issued_credential_scope
from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy

_PASSWORD_WRITE_PATHS = (
    re.compile(r"^/api/admin/users$"),
    re.compile(r"^/api/admin/users/\d+/reset-password$"),
)


def _is_password_write(request: Request) -> bool:
    return request.method.upper() == "POST" and any(
        pattern.fullmatch(request.url.path) for pattern in _PASSWORD_WRITE_PATHS
    )


def _is_user_creation(request: Request) -> bool:
    return request.method.upper() == "POST" and request.url.path == "/api/admin/users"


def _password_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    password = payload.get("password")
    return password if isinstance(password, str) else None


async def enforce_admin_password_request_policy(request: Request) -> None:
    """Validate admin-issued passwords through the production password authority.

    This coroutine remains directly awaitable for internal callers and contract
    tests. The FastAPI dependency wrapper below owns the request-scoped first-
    login context for the duration of the actual user-creation endpoint.
    """

    if not _is_password_write(request):
        return
    try:
        payload = await request.json()
    except Exception:
        # FastAPI/Pydantic owns malformed-body reporting. Do not create a
        # second JSON parser contract here.
        payload = None
    password = _password_from_payload(payload)
    if password is None:
        return
    try:
        validate_admin_password_policy(password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


async def enforce_admin_password_request_policy_dependency(
    request: Request,
) -> AsyncIterator[None]:
    """Bind validation and first-login scope to the canonical admin request."""

    await enforce_admin_password_request_policy(request)
    with administrator_issued_credential_scope(_is_user_creation(request)):
        yield
