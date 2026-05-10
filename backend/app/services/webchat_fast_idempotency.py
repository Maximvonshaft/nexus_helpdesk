from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class IdempotencyKey:
    tenant_key: str
    session_id: str
    client_message_id: str

    def value(self) -> str:
        return f"{self.tenant_key or 'default'}:{self.session_id}:{self.client_message_id}"


def _prune(now: float) -> None:
    expired = [key for key, (created_at, _) in _CACHE.items() if now - created_at > _TTL_SECONDS]
    for key in expired:
        _CACHE.pop(key, None)


def get_fast_reply_idempotent_response(*, tenant_key: str, session_id: str, client_message_id: str) -> dict[str, Any] | None:
    now = time.time()
    _prune(now)
    row = _CACHE.get(IdempotencyKey(tenant_key, session_id, client_message_id).value())
    if not row:
        return None
    return dict(row[1])


def remember_fast_reply_response(*, tenant_key: str, session_id: str, client_message_id: str, response: dict[str, Any]) -> None:
    now = time.time()
    _prune(now)
    _CACHE[IdempotencyKey(tenant_key, session_id, client_message_id).value()] = (now, dict(response))


def reset_fast_reply_idempotency_for_tests() -> None:
    _CACHE.clear()
