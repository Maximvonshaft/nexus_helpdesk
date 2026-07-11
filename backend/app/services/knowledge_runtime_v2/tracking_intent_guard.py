from __future__ import annotations

import re

from . import runtime as _runtime


_POLICY_TERMS = {
    "format",
    "example",
    "policy",
    "rule",
    "guidance",
    "procedure",
    "how to",
    "格式",
    "示例",
    "规则",
    "政策",
    "怎么查",
    "如何查询",
    "richtlinie",
    "regel",
    "politique",
    "règle",
    "politica",
    "regola",
    "formato",
    "política",
    "regla",
    "pravilo",
    "politika",
}
_NO_EVIDENCE_EXPANSION_TERMS = (
    "tracking lookup failed",
    "waybill not found",
    "wrong tracking number",
    "tracking number format",
    "waybill format",
    "客户输入运单号查不到",
    "订单号多输少输",
    "运单号格式",
    "核对单号",
    "CH tracking number format",
)
_NO_EVIDENCE_EXPANSION_SUFFIX = " ".join(_NO_EVIDENCE_EXPANSION_TERMS)
_CURRENT_LOCATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwhere\s+(?:is|are)\s+(?:my|the|this|our)?\s*(?:parcel|package|shipment|waybill)\b",
        r"\b(?:parcel|package|shipment|waybill)\s+(?:location|whereabouts)\b",
        r"\bcurrent\s+location\s+(?:of|for)\s+(?:my|the|this|our)?\s*(?:parcel|package|shipment|waybill)\b",
        r"(?:包裹|快递|运单|物流).{0,12}(?:在哪|哪里|到哪|当前位置)",
        r"(?:在哪|哪里|到哪).{0,12}(?:包裹|快递|运单|物流)",
        r"\bwo\s+ist\s+(?:mein|das|die)?\s*(?:paket|sendung)\b",
        r"\boù\s+est\s+(?:mon|le|la)?\s*(?:colis|envoi)\b",
        r"\bdov['’]?è\s+(?:il|mio)?\s*(?:pacco|spedizione)\b",
        r"\bd[oó]nde\s+est[aá]\s+(?:mi|el|la)?\s*(?:paquete|env[ií]o)\b",
        r"\b(?:gde|gdje)\s+je\s+(?:moj|moja)?\s*(?:paket|pošiljka|posiljka)\b",
    )
)
_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _runtime.is_live_tracking_intent = is_live_tracking_intent_guarded
    _INSTALLED = True


def _customer_intent_text(value: str | None) -> str:
    """Remove the deterministic internal KB expansion before classifying intent.

    ``ai_runtime_context`` appends this suffix only to retrieve static waybill
    guidance when no trusted tracking fact exists.  Classifying that expanded
    retrieval query as customer intent makes the synthetic words ``tracking``
    and ``waybill`` look like a live-status request.  Keep the expansion for
    retrieval, but classify only the original customer prefix (plus any
    separately appended identifier).
    """

    text = _runtime._normalize(value)
    suffix = _runtime._normalize(_NO_EVIDENCE_EXPANSION_SUFFIX)
    if suffix and text.endswith(suffix):
        return text[: -len(suffix)].strip()
    return text


def is_live_tracking_intent_guarded(value: str | None) -> bool:
    text = _customer_intent_text(value)
    if not text:
        return False

    original = text.upper()
    has_tracking = any(term in text for term in _runtime.LIVE_TRACKING_TERMS)
    has_status = any(term in text for term in _runtime.LIVE_STATUS_TERMS)
    has_identifier = any(
        any(character.isdigit() for character in match.group(0))
        for match in _runtime.TRACKING_ID_RE.finditer(original)
    )
    has_policy = any(term in text for term in _POLICY_TERMS)
    has_current_location = any(pattern.search(text) for pattern in _CURRENT_LOCATION_PATTERNS)

    # Static policy, format and identifier-recognition guidance takes precedence
    # over generic tracking vocabulary. An identifier may be an example, so only
    # explicit status or current-location intent may cross the Knowledge/Tracking
    # Truth Layer boundary when policy language is present.
    if has_policy and not has_current_location and (not has_identifier or not has_status):
        return False

    live_signal = (
        (has_identifier and (has_tracking or has_status))
        or has_current_location
        or (has_tracking and has_status)
    )
    return bool(live_signal)
