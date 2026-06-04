from __future__ import annotations

from .schemas import ActionBoundary, DomainQueryUnderstandingResult, EvidenceClass, RankedCandidate


def rerank_candidates(
    candidates: list[RankedCandidate],
    understanding: DomainQueryUnderstandingResult,
) -> list[RankedCandidate]:
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        score = float(candidate.score)
        breakdown = dict(candidate.score_breakdown)

        if understanding.domain and candidate.domain == understanding.domain:
            score += 50.0
            breakdown["domain_match"] = 50.0
        elif understanding.domain and candidate.domain and candidate.domain != understanding.domain:
            score -= 100.0
            breakdown["wrong_domain_penalty"] = -100.0

        if understanding.primary_intent and understanding.primary_intent in candidate.intent_keys:
            score += 90.0
            breakdown["primary_intent_match"] = 90.0
        elif understanding.secondary_intents and set(understanding.secondary_intents).intersection(candidate.intent_keys):
            score += 50.0
            breakdown["secondary_intent_match"] = 50.0

        if understanding.evidence_class and candidate.evidence_class == understanding.evidence_class:
            score += 25.0
            breakdown["evidence_class_match"] = 25.0

        if _action_boundary_conflicts(understanding, candidate):
            score -= 150.0
            breakdown["action_boundary_penalty"] = -150.0

        if candidate.evidence_class == EvidenceClass.LIVE_STATUS and not understanding.requires_tool_boundary:
            score -= 30.0
            breakdown["live_status_without_tool_boundary_penalty"] = -30.0

        ranked.append(candidate.with_score(score, breakdown))

    ranked.sort(key=lambda item: (-item.score, item.item_key))
    return ranked


def _action_boundary_conflicts(
    understanding: DomainQueryUnderstandingResult, candidate: RankedCandidate) -> bool:
    if understanding.requires_tool_boundary and candidate.action_boundary == ActionBoundary.NONE:
        return True
    if understanding.requires_verification and candidate.action_boundary == ActionBoundary.NONE:
        return True
    if understanding.action_boundary in {ActionBoundary.TOOL_REQUIRED, ActionBoundary.VERIFICATION_REQUIRED}:
        return candidate.action_boundary == ActionBoundary.NONE
    return False
