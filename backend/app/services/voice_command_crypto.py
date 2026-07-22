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
            raise RuntimeError("application secret is required for voice commands")
        root_secret = "development-only-voice-command-root"
    derived = hashlib.sha256(
        f"nexus.voice-command.v1\x00{root_secret}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def seal_voice_command_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token = _cipher().encrypt(serialized).decode("ascii")
    return json.dumps({"version": 1, "sealed": token}, separators=(",", ":"))


def open_voice_command_payload(value: str | None) -> dict[str, Any]:
    try:
        envelope = json.loads(value or "{}")
        token = str(envelope["sealed"])
        payload = json.loads(_cipher().decrypt(token.encode("ascii")))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, InvalidToken) as exc:
        raise RuntimeError("voice_command_payload_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("voice_command_payload_invalid")
    return payload
