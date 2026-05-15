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


_DEFAULT_CUSTOMER_REPLY = "A human teammate will review this request."

_RULES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "explicit_human_request",
        (
            "human", "real person", "live agent", "operator", "manual support", "customer service", "support agent",
            "人工", "真人", "人工客服", "转人工", "客服", "专员",
            "mensch", "mitarbeiter", "kundendienst", "berater",
            "humain", "conseiller", "service client",
            "operatore", "assistenza", "persona",
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


def decide_server_handoff_policy(
    *,
    body: str,
    recent_context: list[dict[str, Any]] | None = None,
) -> HandoffPolicyDecision:
    """Return deterministic server authority for WebChat handoff routing.

    This policy is intentionally independent from OpenClaw/model output. The AI
    may still request handoff, but it cannot suppress these server-owned rules.
    """

    joined = _normalize(body)
    context = _customer_context_text(recent_context)
    if context:
        joined = f"{context}\n{joined}"

    for rule_id, phrases, reason, action in _RULES:
        if any(_contains_phrase(joined, phrase) for phrase in phrases):
            return HandoffPolicyDecision(
                handoff_required=True,
                rule_id=rule_id,
                handoff_reason=reason,
                recommended_agent_action=f"[{rule_id}] {action}",
                customer_reply=_DEFAULT_CUSTOMER_REPLY,
            )
    return HandoffPolicyDecision(handoff_required=False)
