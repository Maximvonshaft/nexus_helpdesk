from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class HandoffPolicyDecision:
    handoff_required: bool
    rule_id: str | None = None
    handoff_reason: str | None = None
    recommended_agent_action: str | None = None
    customer_reply: str | None = None


@dataclass(frozen=True)
class ConfiguredHandoffRule:
    rule_id: str
    phrases: tuple[str, ...]
    handoff_reason: str
    recommended_agent_action: str
    customer_reply: str | None = None
    enabled: bool = True


_DEFAULT_CUSTOMER_REPLY = "A human teammate will review this request."

_RULES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "explicit_human_request",
        (
            "speak to a human", "talk to a human", "speak to human", "talk to human", "connect to human", "human please", "speak with a human", "talk with a human",
            "human agent", "human representative", "real person", "live agent", "operator", "representative",
            "manual support", "support agent", "human support", "connect me to an agent", "connect me to a human",
            "speak to an agent", "talk to an agent", "agent please", "representative please",
            "i need an agent", "need an agent", "i want an agent", "want an agent",
            "i need a human", "need a human", "i want a human", "want a human",
            "人工", "真人", "人工客服", "真人客服", "转人工", "转接人工", "我要人工", "找人工", "人工专员",
            "menschlicher berater", "mitarbeiter", "berater", "mit einem mitarbeiter", "mitarbeiter sprechen",
            "humain", "conseiller", "agent humain", "parler à un conseiller",
            "operatore", "persona", "persona reale", "parlare con un operatore",
        ),
        "customer_requested_human_review",
        "Customer explicitly requested human review. Review the conversation and respond with verified information.",
    ),
    (
        "complaint_or_escalation",
        (
            "complaint", "complain", "escalate", "supervisor", "manager", "angry", "unacceptable",
            "投诉", "升级", "主管", "经理", "不满意", "差评",
            "beschwerde", "eskalation", "vorgesetz", "unzufrieden",
            "réclamation", "plainte", "responsable",
            "reclamo", "lamentela", "responsabile",
        ),
        "complaint_requires_human_review",
        "Customer appears to be complaining or escalating. Review priority, shipment state, and customer history.",
    ),
    (
        "refund_compensation_claim",
        (
            "refund", "compensation", "claim", "reimburse", "money back", "pay me", "赔偿", "退款", "索赔", "补偿", "退钱",
            "rückerstattung", "erstattung", "entschädigung", "geld zurück",
            "remboursement", "indemnisation", "dédommagement",
            "rimborso", "risarcimento", "indennizzo",
        ),
        "refund_or_compensation_requires_human_review",
        "Customer is asking for refund, compensation, or claim handling. Do not promise outcome; verify policy and shipment evidence.",
    ),
    (
        "address_change_request",
        (
            "change address", "wrong address", "new address", "address correction", "modify address",
            "改地址", "地址变更", "地址错", "修改地址", "新地址",
            "adresse ändern", "falsche adresse", "neue adresse",
            "changer l'adresse", "mauvaise adresse", "nouvelle adresse",
            "cambiare indirizzo", "indirizzo sbagliato", "nuovo indirizzo",
        ),
        "address_change_requires_human_review",
        "Customer requested address change/correction. Verify whether address change is supported before taking action.",
    ),
    (
        "customs_clearance_issue",
        (
            "customs", "clearance", "duty", "tax", "import", "海关", "清关", "关税", "税费", "进口",
            "zoll", "verzollung", "einfuhr", "douane", "dédouanement", "dogana", "sdoganamento",
        ),
        "customs_issue_requires_human_review",
        "Customer mentioned customs/clearance/tax issue. Verify shipment and customs status before replying.",
    ),
    (
        "lost_damaged_or_missing_parcel",
        (
            "lost", "missing", "not received", "never received", "damaged", "broken", "stolen",
            "丢件", "丢了", "没收到", "未收到", "破损", "损坏", "被偷",
            "verloren", "nicht erhalten", "beschädigt", "gestohlen",
            "perdu", "pas reçu", "endommagé", "volé",
            "perso", "non ricevuto", "danneggiato", "rubato",
        ),
        "lost_or_damaged_parcel_requires_human_review",
        "Customer reported lost, missing, damaged, or stolen parcel. Verify POD, scans, route, and exception records.",
    ),
    (
        "refusal_or_return_request",
        (
            "refuse delivery", "refusal", "return parcel", "send back", "reject delivery", "拒收", "退回", "退件", "不要了",
            "annahme verweigern", "zurücksenden", "retour", "refuser", "retourner", "rifiutare", "restituire",
        ),
        "refusal_or_return_requires_human_review",
        "Customer mentioned refusal or return. Verify supported action and shipment state before responding.",
    ),
)

_WORDY_LATIN = re.compile(r"[\w'-]+", re.UNICODE)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _customer_context_text(recent_context: Iterable[dict[str, Any]] | None) -> str:
    texts: list[str] = []
    for item in recent_context or []:
        if not isinstance(item, dict):
            continue
        role = _normalize(item.get("role"))
        if role not in {"customer", "visitor", "user"}:
            continue
        text = _normalize(item.get("text") if item.get("text") is not None else item.get("body"))
        if text:
            texts.append(text[:500])
    return "\n".join(texts[-4:])


def _contains_phrase(haystack: str, phrase: str) -> bool:
    needle = _normalize(phrase)
    if not needle:
        return False
    if any(ord(ch) > 127 for ch in needle):
        return needle in haystack
    # Keep latin phrase matching deterministic but avoid trivial substring hits
    # such as "claim" inside an unrelated longer token.
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack, flags=re.IGNORECASE) is not None


# ADDRESS_CHANGE_SEMANTIC_MATCH_BEGIN
_ADDRESS_CHANGE_ACTION_RE = re.compile(
    r"\b(?:change|update|modify|correct|fix|replace|edit|revise|changed|updated|modified|corrected|fixed|replaced|edited|revised)\b",
    re.IGNORECASE,
)
_ADDRESS_OBJECT_RE = re.compile(
    r"\b(?:(?:delivery|shipping|receiver|recipient|consignee)\s+)?address\b",
    re.IGNORECASE,
)
_ADDRESS_BAD_OR_NEW_RE = re.compile(
    r"\b(?:wrong|incorrect|bad|invalid|new)\s+(?:(?:delivery|shipping|receiver|recipient|consignee)\s+)?address\b",
    re.IGNORECASE,
)
_ADDRESS_BAD_AFTER_RE = re.compile(
    r"\b(?:(?:delivery|shipping|receiver|recipient|consignee)\s+)?address\s+(?:is|was|looks|seems|appears|entered|typed)?\s*(?:wrong|incorrect|bad|invalid)\b",
    re.IGNORECASE,
)


def _looks_like_address_change_request(haystack: str) -> bool:
    normalized = _normalize(haystack)
    if not normalized:
        return False

    direct_phrases = (
        "change delivery address",
        "change the delivery address",
        "change my delivery address",
        "change shipping address",
        "change the shipping address",
        "change my shipping address",
        "update address",
        "update delivery address",
        "update the delivery address",
        "update my delivery address",
        "update shipping address",
        "update the shipping address",
        "update my shipping address",
        "correct address",
        "correct delivery address",
        "correct the delivery address",
        "correct shipping address",
        "correct the shipping address",
        "fix address",
        "fix delivery address",
        "fix the delivery address",
        "modify delivery address",
        "modify the delivery address",
        "edit delivery address",
        "edit the delivery address",
        "wrong delivery address",
        "wrong shipping address",
        "incorrect delivery address",
        "incorrect shipping address",
        "delivery address is wrong",
        "shipping address is wrong",
        "recipient address is wrong",
        "receiver address is wrong",
        "delivery address is incorrect",
        "shipping address is incorrect",
        "recipient address is incorrect",
        "receiver address is incorrect",
    )
    if any(_contains_phrase(normalized, phrase) for phrase in direct_phrases):
        return True

    latin_text = " ".join(_WORDY_LATIN.findall(normalized))
    if not latin_text:
        return False

    address_matches = list(_ADDRESS_OBJECT_RE.finditer(latin_text))
    if not address_matches:
        return False

    if _ADDRESS_BAD_OR_NEW_RE.search(latin_text) or _ADDRESS_BAD_AFTER_RE.search(latin_text):
        return True

    action_matches = list(_ADDRESS_CHANGE_ACTION_RE.finditer(latin_text))
    if not action_matches:
        return False

    for address_match in address_matches:
        for action_match in action_matches:
            if abs(action_match.start() - address_match.start()) <= 96:
                return True

    return False
# ADDRESS_CHANGE_SEMANTIC_MATCH_END

def _configured_rule_from_payload(item: Any) -> ConfiguredHandoffRule | None:
    if isinstance(item, ConfiguredHandoffRule):
        return item if item.enabled and item.phrases else None
    if not isinstance(item, dict):
        return None
    if item.get("enabled") is False:
        return None
    raw_phrases = item.get("phrases") or item.get("keywords") or []
    if isinstance(raw_phrases, str):
        raw_phrases = [raw_phrases]
    phrases = tuple(str(value).strip() for value in raw_phrases if str(value or "").strip())
    if not phrases:
        return None
    rule_id = str(item.get("rule_id") or item.get("id") or "configured_handoff_rule").strip()[:120]
    reason = str(item.get("handoff_reason") or item.get("reason") or f"{rule_id}_requires_human_review").strip()[:240]
    action = str(item.get("recommended_agent_action") or item.get("action") or "Review the request and respond with verified information.").strip()[:500]
    reply = item.get("customer_reply")
    return ConfiguredHandoffRule(
        rule_id=rule_id,
        phrases=phrases,
        handoff_reason=reason,
        recommended_agent_action=action,
        customer_reply=str(reply).strip()[:500] if reply else None,
        enabled=True,
    )


def _iter_configured_rules(configured_rules: Iterable[Any] | None) -> Iterable[ConfiguredHandoffRule]:
    for item in configured_rules or []:
        rule = _configured_rule_from_payload(item)
        if rule is not None:
            yield rule


def decide_server_handoff_policy(
    *,
    body: str,
    recent_context: list[dict[str, Any]] | None = None,
    configured_rules: Iterable[Any] | None = None,
) -> HandoffPolicyDecision:
    """Return deterministic server authority for WebChat handoff routing.

    Configured rules are evaluated before built-in defaults, but the built-in
    defaults remain the fail-closed safety floor. The AI may still request
    handoff, but it cannot suppress these server-owned rules.
    """

    policy_text = _normalize(body)
    # NEXUSDESK_HANDOFF_BODY_ONLY_CURRENT_TURN
    # Strong server-owned handoff rules are current-turn authority.
    # Historical context remains available to AI/provider context, but it must not
    # trigger a new high-risk handoff after the customer switches intent, e.g.
    # "Refuse delivery" -> "Track parcel".
    _ = recent_context

    for rule in _iter_configured_rules(configured_rules):
        if any(_contains_phrase(policy_text, phrase) for phrase in rule.phrases):
            return HandoffPolicyDecision(
                handoff_required=True,
                rule_id=rule.rule_id,
                handoff_reason=rule.handoff_reason,
                recommended_agent_action=f"[{rule.rule_id}] {rule.recommended_agent_action}",
                customer_reply=rule.customer_reply or _DEFAULT_CUSTOMER_REPLY,
            )

    for rule_id, phrases, reason, action in _RULES:
        if rule_id == "address_change_request" and _looks_like_address_change_request(policy_text):
            return HandoffPolicyDecision(
                handoff_required=True,
                rule_id=rule_id,
                handoff_reason=reason,
                recommended_agent_action=f"[{rule_id}] {action}",
                customer_reply=_DEFAULT_CUSTOMER_REPLY,
            )

        if any(_contains_phrase(policy_text, phrase) for phrase in phrases):
            return HandoffPolicyDecision(
                handoff_required=True,
                rule_id=rule_id,
                handoff_reason=reason,
                recommended_agent_action=f"[{rule_id}] {action}",
                customer_reply=_DEFAULT_CUSTOMER_REPLY,
            )
    return HandoffPolicyDecision(handoff_required=False)
