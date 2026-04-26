from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SafetyLevel = Literal['allow', 'review', 'block']

INTERNAL_DISCLOSURE_KEYWORDS = {
    'secret_key', 'database_url', 'openclaw internal', 'stack trace', 'traceback',
    'access token', 'api token', 'bearer ', 'password', 'passwd', 'private key',
    'jwt', 'x-client-key', 's3_secret_key', 'github token', 'ghp_',
    'mcp', 'openclaw', 'system prompt', 'developer message',
    'tool call',
    'developer instruction',
    'internal instruction',
    'internal context',
    'hidden reasoning',
    'chain of thought',
    '<final',
    '</think',
    '<think',
    'soul.md',
}

# Claims that must not be sent to customers unless backed by exact tool / business evidence.
# Conservative by design: false positives are safer than hallucinated parcel, refund, or SLA claims.
LOGISTICS_FACT_KEYWORDS = {
    'delivered', 'delivery today', 'will arrive', 'arrive today', 'arrive tomorrow',
    'will be delivered', 'delivery tomorrow', 'out for delivery', 'dispatched',
    'lost parcel', 'parcel lost', 'customs cleared', 'customs released',
    'signed', 'signed for', 'successfully signed',
    'refund', 'refunded', 'compensation', 'compensated', 'claim approved',
    '派送成功', '已签收', '签收成功', '今天送达', '明天送达', '预计送达',
    '赔付', '已赔付', '补偿', '已退款', '退款成功', '清关完成',
    '包裹已到', '包裹丢失', '已经发出', '已出库', '已派送', '已送达',
}

AI_SOURCE_MARKERS = {'ai', 'auto_reply', 'ai_auto_reply', 'llm', 'assistant'}

# Only public webchat AI is allowed to send low-risk general replies without manual review.
# All logistics/refund/customs/delivery factual claims still require evidence.
PUBLIC_WEBCHAT_AI_SOURCES = {
    'webchat_ai',
    'webchat_ai_public',
    'public_webchat_ai',
}

MAX_ALLOWED_BODY_CHARS = 4000
MAX_REVIEW_BODY_CHARS = 1200


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    level: SafetyLevel
    reasons: list[str] = field(default_factory=list)
    requires_human_review: bool = False
    normalized_body: str = ''


def _contains_any(haystack: str, needles: set[str]) -> list[str]:
    return sorted(item for item in needles if item in haystack)


def evaluate_outbound_safety(
    ticket,
    body: str | None,
    source: str | None,
    *,
    has_fact_evidence: bool = False,
) -> SafetyDecision:
    """Deterministic pre-send guard for customer-facing outbound messages.

    Policy:
    - Empty, overlong, or sensitive/internal leakage is blocked.
    - Logistics/refund/customs/delivery factual claims require evidence.
    - Generic AI outbound still requires human review.
    - Public webchat AI may send low-risk general replies directly.
    """
    normalized_body = (body or '').strip()
    normalized_lower = normalized_body.lower()
    normalized_source = (source or '').strip().lower()
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
    if logistics_hits and not has_fact_evidence:
        reasons.append(f'logistics factual claim requires evidence: {", ".join(logistics_hits)}')

    is_public_webchat_ai = (
        normalized_source in PUBLIC_WEBCHAT_AI_SOURCES
        or normalized_source.startswith('webchat_ai')
    )
    is_ai_source = (
        normalized_source in AI_SOURCE_MARKERS
        or any(marker in normalized_source for marker in AI_SOURCE_MARKERS)
    )

    if is_ai_source and not is_public_webchat_ai and not has_fact_evidence:
        reasons.append('AI-generated outbound requires human review before direct send')

    if reasons:
        return SafetyDecision(False, 'review', reasons, True, normalized_body)

    return SafetyDecision(True, 'allow', [], False, normalized_body)


def format_safety_reasons(decision: SafetyDecision) -> str:
    return '; '.join(decision.reasons) if decision.reasons else decision.level
