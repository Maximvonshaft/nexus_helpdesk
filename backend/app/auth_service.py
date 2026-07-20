from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from .settings import get_settings

settings = get_settings()
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = settings.access_token_expire_hours
PASSWORD_HASHER = PasswordHasher()


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: int
    session_version: int
    token_id: str | None = None


def hash_password(password: str) -> str:
    """Hash user credentials; request-specific policy is enforced at API boundaries."""

    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHash, VerifyMismatchError):
        return False


def hash_secret(secret: str) -> str:
    return PASSWORD_HASHER.hash(secret)


def verify_secret(secret: str, secret_hash: str) -> bool:
    return verify_password(secret, secret_hash)


def create_access_token(user_id: int, *, session_version: int = 1) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "sv": max(1, int(session_version)),
        "exp": expire,
        "iat": now,
        "nbf": now,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token_claims(token: str) -> Optional[AccessTokenClaims]:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
        sub = payload.get("sub")
        if sub is None:
            return None
        return AccessTokenClaims(
            user_id=int(sub),
            # Tokens issued before this migration are treated as version 1 so
            # deployment does not force an unplanned global logout.
            session_version=max(1, int(payload.get("sv", 1))),
            token_id=str(payload.get("jti")) if payload.get("jti") else None,
        )
    except Exception:
        return None


def decode_access_token(token: str) -> Optional[int]:
    """Compatibility projection retained for callers that only need the user id."""

    claims = decode_access_token_claims(token)
    return claims.user_id if claims is not None else None
