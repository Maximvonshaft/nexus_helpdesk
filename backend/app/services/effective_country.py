from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

GLOBAL_COUNTRY = "GLOBAL"

_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_PHONE_PREFIX_COUNTRIES = (
    ("41", "CH"),
    ("49", "DE"),
    ("33", "FR"),
    ("39", "IT"),
    ("34", "ES"),
    ("44", "GB"),
    ("86", "CN"),
    ("234", "NG"),
    ("233", "GH"),
    ("254", "KE"),
    ("212", "MA"),
    ("20", "EG"),
    ("966", "SA"),
    ("971", "AE"),
    ("92", "PK"),
    ("1", "US"),
)


@dataclass(frozen=True)
class EffectiveCountry:
    country: str
    source: str


def resolve_effective_country(
    ticket: Any = None,
    conversation: Any = None,
    customer: Any = None,
    market_id: int | None = None,
    channel_payload: dict[str, Any] | None = None,
) -> EffectiveCountry:
    payload = channel_payload if isinstance(channel_payload, dict) else {}
    candidates = (
        ("order_destination_country", _first_value(payload, "order_destination_country", "destination_country", "dest_country", "receiver_country", "consignee_country", "to_country")),
        ("order_origin_country", _first_value(payload, "order_origin_country", "origin_country", "sender_country", "from_country")),
        ("ticket_market_country", _ticket_market_country(ticket, payload)),
        ("customer_country", _customer_country(customer, payload)),
        ("selected_support_country", _first_value(payload, "selected_support_country", "support_country", "country_scope")),
        ("whatsapp_phone_country", _whatsapp_phone_country(ticket, conversation, customer, payload)),
        ("browser_ip_country", _first_value(payload, "browser_country", "ip_country", "geo_country", "inferred_country", "cf_ipcountry")),
    )
    for source, value in candidates:
        country = _normalize_country(value)
        if country:
            return EffectiveCountry(country=country, source=source)
    return EffectiveCountry(country=GLOBAL_COUNTRY, source="global_fallback")


def effective_country_payload(value: EffectiveCountry) -> dict[str, str]:
    return {"effective_country": value.country, "country_source": value.source}


def _ticket_market_country(ticket: Any, payload: dict[str, Any]) -> Any:
    direct = _first_value(payload, "ticket_market_country", "market_country", "market_country_code")
    if direct:
        return direct
    if ticket is not None:
        market = getattr(ticket, "market", None)
        if market is not None:
            country = getattr(market, "country_code", None)
            if country:
                return country
        country = getattr(ticket, "country_code", None)
        if country:
            return country
    return None


def _customer_country(customer: Any, payload: dict[str, Any]) -> Any:
    direct = _first_value(payload, "customer_country", "customer_country_code")
    if direct:
        return direct
    if customer is None:
        return None
    for attr in ("country_code", "country", "shipping_country", "default_country"):
        value = getattr(customer, attr, None)
        if value:
            return value
    metadata = getattr(customer, "metadata_json", None)
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = None
    if isinstance(metadata, dict):
        return _first_value(metadata, "country_code", "country", "shipping_country", "default_country")
    return None


def _whatsapp_phone_country(ticket: Any, conversation: Any, customer: Any, payload: dict[str, Any]) -> str | None:
    for value in (
        _first_value(payload, "whatsapp_phone", "sender_phone", "chat_jid", "sender_jid", "phone"),
        getattr(conversation, "visitor_phone", None) if conversation is not None else None,
        getattr(ticket, "preferred_reply_contact", None) if ticket is not None else None,
        getattr(ticket, "source_chat_id", None) if ticket is not None else None,
        getattr(customer, "phone", None) if customer is not None else None,
        getattr(customer, "phone_normalized", None) if customer is not None else None,
    ):
        country = _country_from_phone(value)
        if country:
            return country
    return None


def _country_from_phone(value: Any) -> str | None:
    text = str(value or "")
    if "@" in text:
        text = text.split("@", 1)[0]
    digits = re.sub(r"\D+", "", text)
    if not digits:
        return None
    for prefix, country in _PHONE_PREFIX_COUNTRIES:
        if digits.startswith(prefix):
            return country
    return None


def _first_value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_country(value: Any) -> str | None:
    cleaned = str(value or "").strip().upper()
    if not cleaned:
        return None
    if cleaned in {"*", "ALL", "ANY"}:
        return GLOBAL_COUNTRY
    if cleaned == GLOBAL_COUNTRY:
        return GLOBAL_COUNTRY
    if _COUNTRY_RE.match(cleaned):
        return cleaned
    return None
