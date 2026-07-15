from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_CANONICAL_PREFIXES = (
    "/workspace",
    "/knowledge",
    "/channels",
    "/runtime",
    "/control-tower",
)

_LEGACY_TO_CANONICAL = {
    "/accounts": "/channels",
    "/outbound-email": "/channels",
    "/ai-control": "/knowledge",
    "/bulletins": "/control-tower",
}


def canonical_operator_href(value: object) -> str | None:
    """Project server-generated operator links onto the canonical route graph.

    Compatibility translation happens only on the server. Unknown destinations
    fail closed instead of asking the browser to guess a replacement product.
    """

    candidate = str(value or "").strip()
    if not candidate.startswith("/"):
        return None

    for prefix in _CANONICAL_PREFIXES:
        if candidate == prefix or candidate.startswith(f"{prefix}?") or candidate.startswith(f"{prefix}#"):
            return candidate

    for legacy, canonical in _LEGACY_TO_CANONICAL.items():
        if candidate == legacy or candidate.startswith(f"{legacy}?") or candidate.startswith(f"{legacy}#"):
            return canonical
    return None


def project_control_tower_routes(payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = dict(payload)
    for collection_name in ("manager_actions", "channel_health", "governance_lanes", "template_blocks"):
        rows = []
        for raw in payload.get(collection_name, []) or []:
            row = dict(raw)
            href = canonical_operator_href(row.get("href"))
            row["href"] = href
            if href is None and "enabled" in row:
                row["enabled"] = False
            rows.append(row)
        projected[collection_name] = rows
    return projected
