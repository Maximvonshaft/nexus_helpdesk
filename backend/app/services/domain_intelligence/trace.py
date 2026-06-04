from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import AnswerPlan, DomainGuardDecision, DomainQueryUnderstandingResult, RankedCandidate


@dataclass(frozen=True)
class DomainRuntimeTrace:
    original_query: str
    understanding: DomainQueryUnderstandingResult
    ranked_candidates: tuple[RankedCandidate, ...] = ()
    guard_decisions: tuple[DomainGuardDecision, ...] = ()
    answer_plan: AnswerPlan | None = None
    enforced: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_trace(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "understanding": self.understanding.as_trace(),
            "ranked_candidates": [candidate.as_trace() for candidate in self.ranked_candidates],
            "guard_decisions": [decision.as_trace() for decision in self.guard_decisions],
            "answer_plan": self.answer_plan.as_trace() if self.answer_plan else None,
            "enforced": self.enforced,
            "metadata": self.metadata,
        }
