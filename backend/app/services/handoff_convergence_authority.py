from __future__ import annotations

from typing import Any


def install_handoff_convergence(agent_routing_service_module) -> None:  # noqa: ANN001
    """Converge Text and Voice eligibility on RoutingPlan.current_generation.

    The existing facade remains the only assignment writer. This installer
    replaces two legacy candidate predicates that considered the entire Handoff
    history and therefore defeated bounded retry generations.
    """

    if getattr(agent_routing_service_module, "_r15_convergence_installed", False):
        return

    service = agent_routing_service_module
    core = service._core
    original_prior_voice_offer = core._agent_has_prior_voice_offer

    def generation_has_voice_attempt(
        db,
        *,
        handoff_request_id: int,
        agent_id: int,
    ) -> bool:  # noqa: ANN001
        plan = service.routing_plan_for_request(
            db,
            request_id=int(handoff_request_id),
        )
        if plan is None:
            return original_prior_voice_offer(
                db,
                handoff_request_id=handoff_request_id,
                agent_id=agent_id,
            )
        from ..models_handoff_routing import HandoffRoutingCandidateAttempt

        return bool(
            db.query(HandoffRoutingCandidateAttempt.id)
            .filter(
                HandoffRoutingCandidateAttempt.plan_id == plan.id,
                HandoffRoutingCandidateAttempt.generation
                == plan.current_generation,
                HandoffRoutingCandidateAttempt.agent_id == int(agent_id),
                HandoffRoutingCandidateAttempt.channel_kind == "voice",
            )
            .first()
        )

    def eligible_text_request_for_agent(
        db,
        *,
        user,
    ) -> tuple[Any, Any, Any] | None:  # noqa: ANN001
        voice_exists = (
            db.query(core.WebchatVoiceSession.id)
            .filter(
                core.WebchatVoiceSession.conversation_id
                == core.WebchatHandoffRequest.conversation_id,
                core.WebchatVoiceSession.status.in_(
                    sorted(core.VOICE_OPEN_SESSION_STATUSES)
                ),
            )
            .exists()
        )
        query = (
            db.query(
                core.WebchatHandoffRequest,
                core.WebchatConversation,
                core.ConversationControl,
            )
            .join(
                core.WebchatConversation,
                core.WebchatConversation.id
                == core.WebchatHandoffRequest.conversation_id,
            )
            .join(
                core.ConversationControl,
                core.ConversationControl.conversation_id
                == core.WebchatConversation.id,
            )
            .filter(
                core.WebchatHandoffRequest.status == "requested",
                core.WebchatConversation.status == "open",
                core.ConversationControl.country_code.is_not(None),
                ~voice_exists,
            )
            .order_by(
                core.WebchatHandoffRequest.requested_at.asc(),
                core.WebchatHandoffRequest.id.asc(),
            )
            .limit(100)
        )
        for request_row, conversation, control in core._lock(query, db).all():
            plan = service.ensure_handoff_routing_plan(
                db,
                request_row=request_row,
            )
            if plan is None:
                declined = bool(
                    db.query(core.WebchatHandoffDecision.id)
                    .filter(
                        core.WebchatHandoffDecision.request_id == request_row.id,
                        core.WebchatHandoffDecision.actor_id == user.id,
                        core.WebchatHandoffDecision.decision == "declined",
                    )
                    .first()
                )
                if declined:
                    continue
            if service._candidate_authorized(
                db,
                plan=plan,
                control=control,
                user=user,
                channel_kind="text",
            ):
                return request_row, conversation, control
        return None

    core._agent_has_prior_voice_offer = generation_has_voice_attempt
    service._eligible_text_request_for_agent = eligible_text_request_for_agent
    service._r15_convergence_installed = True


__all__ = ["install_handoff_convergence"]
