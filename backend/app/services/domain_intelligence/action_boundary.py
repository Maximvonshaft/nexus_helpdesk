from __future__ import annotations

from .schemas import ActionBoundary, DomainQueryUnderstandingResult


def action_boundary_required(understanding: DomainQueryUnderstandingResult) -> bool:
    return understanding.action_boundary in {
        ActionBoundary.VERIFICATION_REQUIRED,
        ActionBoundary.TOOL_REQUIRED,
        ActionBoundary.HUMAN_REQUIRED,
        ActionBoundary.FORBIDDEN,
    }


def action_boundary_reason(understanding: DomainQueryUnderstandingResult) -> str:
    if understanding.action_boundary == ActionBoundary.VERIFICATION_REQUIRED:
        return "verification_required"
    if understanding.action_boundary == ActionBoundary.TOOL_REQUIRED:
        return "tool_required"
    if understanding.action_boundary == ActionBoundary.HUMAN_REQUIRED:
        return "human_required"
    if understanding.action_boundary == ActionBoundary.FORBIDDEN:
        return "forbidden"
    return "none"
