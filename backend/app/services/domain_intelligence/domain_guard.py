from __future__ import annotations

from .schemas import ActionBoundary, DomainGuardDecision, DomainQueryUnderstandingResult, EvidenceClass, RankedCandidate


def evaluate_candidate(
    candidate: RankedCandidate,
    understanding: DomainQueryUnderstandingResult,
) -> DomainGuardDecision:
    if not understanding.has_business_intent:
        return DomainGuardDecision(False, "no_business_intent", "info", candidate)

    if understanding.domain and candidate.domain and candidate.domain != understanding.domain:
        return DomainGuardDecision(False, "wrong_domain", "high", candidate)

    if understanding.primary_intent and candidate.intent_keys:
        compatible = understanding.primary_intent in candidate.intent_keys or bool(set(understanding.secondary_intents).intersection(candidate.intent_keys))
        if not compatible:
            return DomainGuardDecision(False, "wrong_intent", "high", candidate)

    if understanding.requires_tool_boundary and candidate.evidence_class not in {EvidenceClass.TOOL_RESULT, EvidenceClass.LIVE_STATUS}:
        if candidate.action_boundary == ActionBoundary.NONE:
            return DomainGuardDecision(False, "tool_boundary_required", "high", candidate)

    if understanding.requires_verification and candidate.action_boundary == ActionBoundary.NONE:
        return DomainGuardDecision(False, "verification_boundary_required", "high", candidate)

    if candidate.action_boundary == ActionBoundary.FORBIDDEN:
        return DomainGuardDecision(False, "candidate_forbidden", "high", candidate)

    return DomainGuardDecision(True, "candidate_allowed", "info", candidate)


def filter_allowed_candidates(
    candidates: list[RankedCandidate],
    understanding: DomainQueryUnderstandingResult,
) -> tuple[list[RankedCandidate], list[DomainGuardDecision]]:
    decisions = [evaluate_candidate(candidate, understanding) for candidate in candidates]
    allowed = [decision.candidate for decision in decisions if decision.allowed and decision.candidate]
    return allowed, decisions
