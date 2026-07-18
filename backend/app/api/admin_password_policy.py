from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, Request, status

from ..services.password_policy import PasswordPolicyError, validate_admin_password_policy

_PASSWORD_WRITE_PATHS = (
    re.compile(r"^/api/admin/users$"),
    re.compile(r"^/api/admin/users/\d+/reset-password$"),
)


def _is_password_write(request: Request) -> bool:
    return request.method.upper() == "POST" and any(
        pattern.fullmatch(request.url.path) for pattern in _PASSWORD_WRITE_PATHS
    )


def _password_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    password = payload.get("password")
    return password if isinstance(password, str) else None


async def enforce_admin_password_request_policy(request: Request) -> None:
    """Fail closed on admin password writes before the endpoint mutates state."""

    if not _is_password_write(request):
        return
    try:
        payload = await request.json()
    except Exception:
        # FastAPI/Pydantic owns malformed-body reporting. Do not create a
        # second JSON parser contract here.
        return
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
