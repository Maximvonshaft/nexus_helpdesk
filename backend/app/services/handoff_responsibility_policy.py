from __future__ import annotations

from typing import Any

from .permissions import (
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
)

_OPEN_HANDOFF_STATUSES = frozenset({"requested", "accepted"})


def can_resume_handoff(
    *,
    handoff: Any,
    conversation: Any,
    user_id: int,
    capabilities: set[str],
) -> bool:
    """Return the single server-authoritative AI-resume decision.

    Supervisors with the explicit resume capability may return any open Handoff
    to AI inside their separately authorized queue scope. Ordinary operators may
    only release a Handoff they currently own in both the durable Request and the
    Conversation occupancy snapshot. Ticket association never selects a second
    responsibility policy.
    """

    if handoff.status not in _OPEN_HANDOFF_STATUSES:
        return False
    if CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities:
        return True
    return bool(
        handoff.status == "accepted"
        and handoff.assigned_agent_id == user_id
        and conversation.active_agent_id == user_id
        and CAP_WEBCHAT_HANDOFF_RELEASE in capabilities
    )


__all__ = ["can_resume_handoff"]
