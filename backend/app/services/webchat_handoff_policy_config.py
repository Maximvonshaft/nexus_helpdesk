from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import AIConfigResource, Market

CONFIG_TYPE = "webchat_handoff_policy"


def _country_for_market(db: Session, market_id: int | None) -> str | None:
    if market_id is None:
        return None
    market = db.query(Market).filter(Market.id == market_id).first()
    return market.country_code if market else None


def _content_rules(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_rules = payload.get("rules") or payload.get("handoff_rules") or []
    elif isinstance(payload, list):
        raw_rules = payload
    else:
        raw_rules = []
    return [item for item in raw_rules if isinstance(item, dict)]


def _resource_priority(row: AIConfigResource, *, market_id: int | None, country_code: str | None) -> int:
    scope_type = (row.scope_type or "global").strip().lower()
    scope_value = (row.scope_value or "").strip().upper()
    normalized_country = (country_code or "").strip().upper()
    if market_id is not None and row.market_id == market_id:
        return 0
    if normalized_country and scope_type in {"country", "country_code"} and scope_value == normalized_country:
        return 1
    if scope_type == "global" and row.market_id is None:
        return 2
    return 9


def load_webchat_handoff_rules(
    db: Session,
    *,
    market_id: int | None = None,
    country_code: str | None = None,
) -> list[dict[str, Any]]:
    """Load published operator-configured Fast Lane handoff rules.

    Expected published_content_json shape:
      {"rules": [{"rule_id": "...", "phrases": ["..."], ...}]}

    Built-in policy rules remain the fail-closed safety floor when this returns
    no rules or when configured rules do not match.
    """

    resolved_country = (country_code or _country_for_market(db, market_id) or "").strip().upper() or None
    rows = (
        db.query(AIConfigResource)
        .filter(
            AIConfigResource.config_type == CONFIG_TYPE,
            AIConfigResource.is_active.is_(True),
            AIConfigResource.published_version > 0,
            AIConfigResource.published_content_json.is_not(None),
        )
        .all()
    )
    applicable = [
        row
        for row in rows
        if _resource_priority(row, market_id=market_id, country_code=resolved_country) < 9
    ]
    applicable.sort(key=lambda row: (_resource_priority(row, market_id=market_id, country_code=resolved_country), row.id))

    rules: list[dict[str, Any]] = []
    for row in applicable:
        for item in _content_rules(row.published_content_json):
            enriched = dict(item)
            enriched.setdefault("config_resource_key", row.resource_key)
            enriched.setdefault("config_resource_id", row.id)
            rules.append(enriched)
    return rules
