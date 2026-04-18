from __future__ import annotations

import re
from typing import Optional


def normalize_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def normalize_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith('+'):
        digits = '+' + re.sub(r'\D', '', raw[1:])
    else:
        digits = re.sub(r'\D', '', raw)
    return digits or None
