from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SafetyLevel = Literal['allow', 'review', 'block']

INTERNAL_DISCLOSURE_KEYWORDS = {
    'secret_key', 'database_url', 'openclaw internal', 'stack trace', 'traceback',
    'access token', 'api token', 'bearer ', 'password', 'passwd', 'private key',
    'jwt', 'x-client-key', 's3_secret_key', 'github token', 'ghp_',
}

LOGISTICS_FACT_KEYWORDS = {
    'delivered', 'delivery today', 'will arrive', 'arrive today', 'arrive tomorrow',
    'lost parcel', 'parcel lost', 'compensation', 'customs cleared', 'dispatched',
    '派送成功', '已签收', '今天送达', '明天送达', '赔付', '清关完成',
    '包裹已到', '包裹丢失', '已经发出', '已出库', '已派送', '已送达',
}

# These claims are materially higher risk in a logistics support setting. Even when
# an operator has evidence, they should be forced through human review rather than
# bypassing the queue guard automatically.
HIGH_RISK_LOGISTICS_FACT_KEYWORDS = {
    'lost parcel', 'parcel lost', 'compensation', 'customs cleared',
    '赔付', '清关完成', '包裹丢失',
}

AI_SOURCE_MARKERS = {'ai', 'auto_reply', 'ai_auto_reply', 'llm', 'assistant'}
MAX_ALLOWED_BODY_CHARS = 4000
MAX_REVIEW_BODY_CHARS = 1200
EVIDENCE_KEYS = {
    'evidence_source', 'tracking_number', 'event_code', 'event_time',
    'checked_by', 'checked_at', 'evidence_summary',
}


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    level: SafetyLevel
    reasons: list[str] = field(default_factory=list)
    requires_human_review: bool = False
    normalized_body: str = ''


def _contains_any(haystack: str, needles: set[str]) -> list[str]:
    return sorted(item for item in needles if item in haystack)


def _has_structured_fact_evidence(fact_evidence: dict[str, Any] | None) -> bool:
    if not isinstance(fact_evidence, dict):
        return False
    return any(str(fact_evidence.get(key) or '').strip() for key in EVIDENCE_KEYS)


def evaluate_outbound_safety(
    ticket,
    body: str | None,
    source: str | None,
    *,
    has_fact_evidence: bool = False,
    fact_evidence: dict[str, Any] | None = None,
) -> SafetyDecision:
    """Deterministic pre-send guard for customer-facing outbound messages.

    The guard deliberately separates three cases:
    - unsafe content is blocked outright;
    - logistics factual claims need evidence before they can bypass review;
    - high-risk logistics claims stay in review even when evidence exists.
    """
    normalized_body = (body or '').strip()
    normalized_lower = normalized_body.lower()
    normalized_source = (source or '').strip().lower()
    evidence_present = has_fact_evidence or _has_structured_fact_evidence(fact_evidence)
    reasons: list[str] = []

    if not normalized_body:
        return SafetyDecision(False, 'block', ['empty outbound body'], False, normalized_body)

    if len(normalized_body) > MAX_ALLOWED_BODY_CHARS:
        return SafetyDecision(False, 'block', ['outbound body exceeds hard safety length limit'], False, normalized_body)

    if len(normalized_body) > MAX_REVIEW_BODY_CHARS:
        reasons.append('outbound body is long and requires human review')

    internal_hits = _contains_any(normalized_lower, INTERNAL_DISCLOSURE_KEYWORDS)
    if internal_hits:
        return SafetyDecision(False, 'block', [f'internal/sensitive term detected: {", ".join(internal_hits)}'], False, normalized_body)

    logistics_hits = _contains_any(normalized_lower, LOGISTICS_FACT_KEYWORDS)
    high_risk_hits = _contains_any(normalized_lower, HIGH_RISK_LOGISTICS_FACT_KEYWORDS)
    if high_risk_hits:
        reasons.append(f'high-risk logistics claim requires human review: {", ".join(high_risk_hits)}')
    elif logistics_hits and not evidence_present:
        reasons.append(f'logistics factual claim requires evidence: {", ".join(logistics_hits)}')

    if normalized_source in AI_SOURCE_MARKERS or any(marker in normalized_source for marker in AI_SOURCE_MARKERS):
        if not evidence_present:
            reasons.append('AI-generated outbound requires human review before direct send')
        else:
            reasons.append('AI-generated outbound with evidence still requires human review')

    if reasons:
        return SafetyDecision(False, 'review', reasons, True, normalized_body)

    return SafetyDecision(True, 'allow', [], False, normalized_body)


def format_safety_reasons(decision: SafetyDecision) -> str:
    return '; '.join(decision.reasons) if decision.reasons else decision.level
