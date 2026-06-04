from __future__ import annotations

from .schemas import ActionBoundary, AnswerPlan, AnswerPlanType, DomainQueryUnderstandingResult, EvidenceClass, RankedCandidate


def plan_answer(understanding: DomainQueryUnderstandingResult, *, selected_candidate: RankedCandidate | None = None) -> AnswerPlan:
    if not understanding.has_business_intent:
        return AnswerPlan(plan_type=AnswerPlanType.SAFE_GENERAL_REPLY, reason="no_business_intent", safe_to_answer_from_kb=False)

    boundary = understanding.action_boundary
    evidence_class = understanding.evidence_class

    if boundary == ActionBoundary.FORBIDDEN:
        return AnswerPlan(
            plan_type=AnswerPlanType.DENY_UNSUPPORTED,
            reason="operation_not_supported",
            evidence_class=evidence_class,
            action_boundary=boundary,
            safe_to_answer_from_kb=False,
        )

    if understanding.requires_verification:
        return AnswerPlan(
            plan_type=AnswerPlanType.TOOL_PREPARE if understanding.requires_tool_boundary else AnswerPlanType.CLARIFY,
            reason="verification_required",
            requires_verification=True,
            requires_tool=understanding.requires_tool_boundary,
            allowed_tools=_candidate_tools(selected_candidate),
            evidence_class=evidence_class,
            action_boundary=boundary,
            safe_to_answer_from_kb=False,
        )

    if understanding.requires_tool_boundary:
        return AnswerPlan(
            plan_type=AnswerPlanType.TOOL_CALL,
            reason="tool_boundary_required",
            requires_tool=True,
            allowed_tools=_candidate_tools(selected_candidate),
            evidence_class=evidence_class,
            action_boundary=boundary,
            safe_to_answer_from_kb=False,
        )

    if selected_candidate and selected_candidate.evidence_class in {EvidenceClass.POLICY, EvidenceClass.FAQ, EvidenceClass.SOP, EvidenceClass.STRUCTURED_FACT}:
        plan_type = AnswerPlanType.DIRECT_ANSWER if selected_candidate.evidence_class == EvidenceClass.STRUCTURED_FACT else AnswerPlanType.GUIDED_ANSWER
        return AnswerPlan(
            plan_type=plan_type,
            reason="safe_knowledge_answer",
            evidence_class=selected_candidate.evidence_class,
            action_boundary=selected_candidate.action_boundary,
            safe_to_answer_from_kb=True,
        )

    return AnswerPlan(
        plan_type=AnswerPlanType.GUIDED_ANSWER,
        reason="business_intent_without_selected_evidence",
        evidence_class=evidence_class,
        action_boundary=boundary,
        safe_to_answer_from_kb=True,
    )


def _candidate_tools(candidate: RankedCandidate | None) -> tuple[str, ...]:
    if not candidate or not isinstance(candidate.metadata, dict):
        return ()
    tools = candidate.metadata.get("tool_keys")
    if isinstance(tools, (list, tuple)):
        return tuple(str(item) for item in tools if str(item).strip())
    return ()
