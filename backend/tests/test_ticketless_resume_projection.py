from __future__ import annotations

from types import SimpleNamespace

from app.services.conversation_operator_service import _handoff_payload
from app.services.permissions import (
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
)


def _handoff(*, assigned_agent_id: int, status: str = "accepted"):
    return SimpleNamespace(
        id=1,
        assigned_agent_id=assigned_agent_id,
        accepted_by_user_id=assigned_agent_id,
        status=status,
        reason_text=None,
        reason_code="needs_human",
        source="ai_runtime",
        trigger_type="runtime_handoff",
        recommended_agent_action=None,
        requested_at=None,
        accepted_at=None,
        closed_at=None,
    )


def _conversation(*, active_agent_id: int | None):
    return SimpleNamespace(
        id=10,
        public_id="ticketless-resume-projection",
        active_agent_id=active_agent_id,
    )


def test_ticketless_resume_projection_is_owner_and_occupancy_aware():
    owner = SimpleNamespace(id=7)
    other = SimpleNamespace(id=8)
    handoff = _handoff(assigned_agent_id=owner.id)
    active_conversation = _conversation(active_agent_id=owner.id)

    owner_payload = _handoff_payload(
        handoff=handoff,
        conversation=active_conversation,
        user=owner,
        capabilities={CAP_WEBCHAT_HANDOFF_ACCEPT},
    )
    other_payload = _handoff_payload(
        handoff=handoff,
        conversation=active_conversation,
        user=other,
        capabilities={CAP_WEBCHAT_HANDOFF_ACCEPT},
    )
    stale_owner_payload = _handoff_payload(
        handoff=handoff,
        conversation=_conversation(active_agent_id=None),
        user=owner,
        capabilities={CAP_WEBCHAT_HANDOFF_ACCEPT},
    )
    supervisor_payload = _handoff_payload(
        handoff=handoff,
        conversation=_conversation(active_agent_id=None),
        user=other,
        capabilities={CAP_WEBCHAT_HANDOFF_RESUME_AI},
    )

    assert owner_payload["can_resume_ai"] is True
    assert other_payload["can_resume_ai"] is False
    assert stale_owner_payload["can_resume_ai"] is False
    assert supervisor_payload["can_resume_ai"] is True
