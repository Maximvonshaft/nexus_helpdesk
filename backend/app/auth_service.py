from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from sqlalchemy.orm import Session

from .models import User
from .services.permissions import capability_fingerprint
from .settings import get_settings

settings = get_settings()
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = settings.access_token_expire_hours
PASSWORD_HASHER = PasswordHasher()


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: int
    issued_at: datetime
    user_version: datetime | None
    policy_fingerprint: str | None
    token_id: str | None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        if not normalized:
            return None
        return _utc(datetime.fromisoformat(normalized))
    return None


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


def create_access_token(
    user_id: int,
    user_updated_at: datetime | None = None,
    policy_fingerprint: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": now,
        "nbf": now,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid4().hex,
    }
    if user_updated_at is not None:
        payload["uv"] = _utc(user_updated_at).isoformat()
    if policy_fingerprint:
        payload["pf"] = policy_fingerprint
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token_claims(token: str) -> AccessTokenClaims | None:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
        sub = payload.get("sub")
        issued_at = _parse_datetime(payload.get("iat"))
        if sub is None or issued_at is None:
            return None
        return AccessTokenClaims(
            user_id=int(sub),
            issued_at=issued_at,
            user_version=_parse_datetime(payload.get("uv")),
            policy_fingerprint=str(payload.get("pf")) if payload.get("pf") else None,
            token_id=str(payload.get("jti")) if payload.get("jti") else None,
        )
    except Exception:
        return None


def decode_access_token(token: str) -> Optional[int]:
    claims = decode_access_token_claims(token)
    return claims.user_id if claims is not None else None


def access_token_is_current(user: User, claims: AccessTokenClaims) -> bool:
    updated_at = _utc(user.updated_at)
    if claims.user_version is not None:
        return claims.user_version == updated_at
    return int(claims.issued_at.timestamp()) >= int(updated_at.timestamp())


def load_current_user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    claims = decode_access_token_claims(token)
    if claims is None:
        return None
    user = db.query(User).filter(User.id == claims.user_id, User.is_active.is_(True)).first()
    if user is None or not access_token_is_current(user, claims):
        return None
    if claims.policy_fingerprint is not None and claims.policy_fingerprint != capability_fingerprint(user, db):
        return None
    return user
