from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_utc(value: Optional[datetime]) -> Optional[str]:
    value_utc = ensure_utc(value)
    if value_utc is None:
        return None
    return value_utc.isoformat().replace('+00:00', 'Z')
