from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..settings import get_settings


def _cipher() -> Fernet:
    settings = get_settings()
    root_secret = settings.jwt_secret_key
    if not root_secret:
        if settings.app_env == "production":
            raise RuntimeError("application secret is required for telephony events")
        root_secret = "development-only-telephony-event-root"
    derived = hashlib.sha256(
        f"nexus.telephony-event.v1\x00{root_secret}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def seal_telephony_event_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _cipher().encrypt(serialized).decode("ascii")


def open_telephony_event_payload(token: str) -> dict[str, Any]:
    try:
        value = json.loads(_cipher().decrypt(token.encode("ascii")))
    except (InvalidToken, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("telephony_event_payload_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("telephony_event_payload_invalid")
    return value
