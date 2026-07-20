from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from uuid import uuid4

import jwt
from sqlalchemy.orm import Session

from ..models import User
from ..models_identity_policy import UserCredentialPolicy
from ..settings import get_settings
from ..utils.time import utc_now
from .credential_policy_service import ensure_credential_policy
from .secret_crypto import SecretCryptoService
from ..auth_service import hash_secret, verify_secret

TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
MFA_CHALLENGE_MINUTES = 5
RECOVERY_CODE_COUNT = 10

settings = get_settings()


@dataclass(frozen=True)
class MfaChallengeClaims:
    user_id: int
    user_version: datetime
    token_id: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _crypto() -> SecretCryptoService:
    return SecretCryptoService.identity_mfa()


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    normalized = secret.strip().replace(" ", "").upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def totp_code(secret: str, step: int) -> str:
    digest = hmac.new(
        _decode_secret(secret),
        struct.pack(">Q", step),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(
    secret: str,
    code: str,
    *,
    last_used_step: int | None = None,
    timestamp: float | None = None,
) -> int | None:
    normalized = "".join(character for character in str(code) if character.isdigit())
    if len(normalized) != TOTP_DIGITS:
        return None
    current_step = int((timestamp if timestamp is not None else time.time()) // TOTP_PERIOD_SECONDS)
    for step in (current_step, current_step - 1, current_step + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(totp_code(secret, step), normalized):
            return step
    return None


def build_otpauth_uri(*, username: str, secret: str) -> str:
    issuer = "Nexus OSR"
    label = quote(f"{issuer}:{username}", safe="")
    query = urlencode({
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": str(TOTP_DIGITS),
        "period": str(TOTP_PERIOD_SECONDS),
    })
    return f"otpauth://totp/{label}?{query}"


def generate_recovery_codes() -> list[str]:
    codes: list[str] = []
    while len(codes) < RECOVERY_CODE_COUNT:
        raw = secrets.token_hex(5).upper()
        code = f"{raw[:5]}-{raw[5:]}"
        if code not in codes:
            codes.append(code)
    return codes


def _normalize_recovery_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _hash_recovery_codes(codes: list[str]) -> str:
    return json.dumps([hash_secret(_normalize_recovery_code(code)) for code in codes])


def _recovery_hashes(policy: UserCredentialPolicy) -> list[str]:
    if not policy.mfa_recovery_codes_json:
        return []
    try:
        value = json.loads(policy.mfa_recovery_codes_json)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def recovery_codes_remaining(policy: UserCredentialPolicy) -> int:
    return len(_recovery_hashes(policy))


def create_mfa_challenge_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "uv": _utc(user.updated_at).isoformat(),
        "typ": "mfa_challenge",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=MFA_CHALLENGE_MINUTES),
        "iss": settings.jwt_issuer,
        "aud": f"{settings.jwt_audience}:mfa",
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_mfa_challenge_token(token: str) -> MfaChallengeClaims | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            audience=f"{settings.jwt_audience}:mfa",
            issuer=settings.jwt_issuer,
        )
        if payload.get("typ") != "mfa_challenge":
            return None
        user_id = int(payload["sub"])
        user_version = datetime.fromisoformat(str(payload["uv"]).replace("Z", "+00:00"))
        token_id = str(payload["jti"])
        return MfaChallengeClaims(
            user_id=user_id,
            user_version=_utc(user_version),
            token_id=token_id,
        )
    except Exception:
        return None


def begin_mfa_setup(db: Session, user: User) -> tuple[UserCredentialPolicy, str, str]:
    policy = ensure_credential_policy(db, user.id)
    secret = generate_totp_secret()
    encrypted = _crypto().encrypt(secret)
    if not encrypted:
        raise RuntimeError("MFA secret encryption failed")
    policy.mfa_pending_secret_encrypted = encrypted
    policy.updated_at = utc_now()
    db.flush()
    return policy, secret, build_otpauth_uri(username=user.username, secret=secret)


def cancel_mfa_setup(db: Session, user_id: int) -> UserCredentialPolicy:
    policy = ensure_credential_policy(db, user_id)
    policy.mfa_pending_secret_encrypted = None
    policy.updated_at = utc_now()
    db.flush()
    return policy


def confirm_mfa_setup(db: Session, user_id: int, code: str) -> tuple[UserCredentialPolicy, list[str]]:
    policy = ensure_credential_policy(db, user_id)
    secret = _crypto().decrypt(policy.mfa_pending_secret_encrypted)
    if not secret:
        raise ValueError("MFA setup has not been started")
    step = verify_totp(secret, code)
    if step is None:
        raise ValueError("Invalid authenticator code")

    recovery_codes = generate_recovery_codes()
    now = utc_now()
    policy.mfa_enabled = True
    policy.mfa_secret_encrypted = policy.mfa_pending_secret_encrypted
    policy.mfa_pending_secret_encrypted = None
    policy.mfa_recovery_codes_json = _hash_recovery_codes(recovery_codes)
    policy.mfa_confirmed_at = now
    policy.mfa_last_verified_at = now
    policy.mfa_last_used_step = step
    policy.updated_at = now
    db.flush()
    return policy, recovery_codes


def verify_mfa_credential(
    db: Session,
    policy: UserCredentialPolicy,
    credential: str,
) -> str | None:
    if not policy.mfa_enabled:
        return None
    secret = _crypto().decrypt(policy.mfa_secret_encrypted)
    if not secret:
        raise RuntimeError("MFA is enabled but no encrypted secret is available")

    step = verify_totp(secret, credential, last_used_step=policy.mfa_last_used_step)
    now = utc_now()
    if step is not None:
        policy.mfa_last_used_step = step
        policy.mfa_last_verified_at = now
        policy.updated_at = now
        db.flush()
        return "totp"

    normalized = _normalize_recovery_code(credential)
    hashes = _recovery_hashes(policy)
    for index, value_hash in enumerate(hashes):
        if verify_secret(normalized, value_hash):
            hashes.pop(index)
            policy.mfa_recovery_codes_json = json.dumps(hashes)
            policy.mfa_last_verified_at = now
            policy.updated_at = now
            db.flush()
            return "recovery_code"
    return None


def regenerate_recovery_codes(
    db: Session,
    policy: UserCredentialPolicy,
) -> list[str]:
    if not policy.mfa_enabled:
        raise ValueError("MFA is not enabled")
    codes = generate_recovery_codes()
    policy.mfa_recovery_codes_json = _hash_recovery_codes(codes)
    policy.updated_at = utc_now()
    db.flush()
    return codes


def clear_mfa(db: Session, user_id: int) -> UserCredentialPolicy:
    policy = ensure_credential_policy(db, user_id)
    now = utc_now()
    policy.mfa_enabled = False
    policy.mfa_secret_encrypted = None
    policy.mfa_pending_secret_encrypted = None
    policy.mfa_recovery_codes_json = None
    policy.mfa_confirmed_at = None
    policy.mfa_last_verified_at = None
    policy.mfa_last_used_step = None
    policy.updated_at = now
    db.flush()
    return policy


def mfa_status_payload(policy: UserCredentialPolicy) -> dict:
    return {
        "enabled": bool(policy.mfa_enabled),
        "setup_pending": bool(policy.mfa_pending_secret_encrypted),
        "confirmed_at": policy.mfa_confirmed_at,
        "last_verified_at": policy.mfa_last_verified_at,
        "recovery_codes_remaining": recovery_codes_remaining(policy),
    }
