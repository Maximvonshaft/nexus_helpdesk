from __future__ import annotations

import re
from typing import Any

TRACKING_CONTEXT_RE = re.compile(
    r"\b(track|tracking|waybill|parcel|package|shipment|delivery|where is|status|order|recipient|received|receive|not received|did not receive)\b|"
    r"单号|运单|物流|快递|包裹|收件人|没收到|没有收到|签收|派送|配送|查件|查询|订单号|订单",
    re.IGNORECASE,
)


def normalize_tracking_identifier(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def looks_like_tracking_identifier(
    value: Any,
    *,
    context: str | None = None,
    require_context_for_generic: bool = False,
) -> bool:
    token = normalize_tracking_identifier(value)
    if len(token) < 10:
        return False
    digits = sum(char.isdigit() for char in token)
    letters = sum(char.isalpha() for char in token)
    if not digits or not letters:
        return False
    if token.startswith("CH") and digits >= 6:
        return True
    if len(token) < 12 or digits < 6:
        return False
    if require_context_for_generic:
        return bool(TRACKING_CONTEXT_RE.search(context or ""))
    return True


def tracking_context_present(value: str) -> bool:
    return bool(TRACKING_CONTEXT_RE.search(value or ""))
