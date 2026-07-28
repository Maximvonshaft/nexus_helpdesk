#!/usr/bin/env python3
"""Apply the audited R6 routing convergence as exact, fail-closed replacements."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"routing_patch_match_invalid:{path}:{count}:{old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: str, old: str, new: str, *, minimum: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count < minimum:
        raise SystemExit(f"routing_patch_match_missing:{path}:{old!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


AGENT = "backend/app/services/agent_routing_service.py"
CORE = "backend/app/services/webchat_handoff_service_core.py"
FACADE = "backend/app/services/webchat_handoff_service.py"
WORKFLOW = ".github/workflows/canonical-acceptance.yml"

replace_once(
    AGENT,
    "from ..models import Ticket, User\n",
    "from ..enums import ConversationState, TicketStatus\n"
    "from ..models import Ticket, User\n",
)
replace_once(
    AGENT,
    "from .conversation_first_service import ensure_conversation_control\n",
    "from .conversation_first_service import ensure_conversation_control\n"
    "from .handoff_routing_authority import (\n"
    "    candidate_is_authorized,\n"
    "    close_routing_plan,\n"
    "    eligible_agents,\n"
    "    ensure_handoff_routing_plan,\n"
    "    mark_plan_assigned,\n"
    "    record_candidate_attempt,\n"
    "    routing_plan_for_request,\n"
    "    routing_projection,\n"
    "    schedule_retry_or_exhaust,\n"
    "    update_attempt_by_external_ref,\n"
    ")\n",
)

replace_once(
    AGENT,
    '''def _scope_grant_exists(
    db: Session,
    *,
    user: User,
    control: ConversationControl,
) -> bool:
    if not control.country_code:
        return False
    return bool(
        db.query(OperatorQueueScopeGrant.id)
        .filter(
            OperatorQueueScopeGrant.user_id == user.id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code
            == control.country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )
''',
    '''def _scope_grant_exists(
    db: Session,
    *,
    user: User,
    control: ConversationControl,
    request_row: WebchatHandoffRequest | None = None,
    channel_kind: str = "manual",
) -> bool:
    plan = (
        ensure_handoff_routing_plan(db, request_row=request_row)
        if request_row is not None
        else None
    )
    return candidate_is_authorized(
        db,
        plan=plan,
        control=control,
        user=user,
        channel_kind=channel_kind,
        exclude_attempted=False,
    )
''',
)
replace_once(
    AGENT,
    '''        .filter(
            OperatorTask.webchat_conversation_id == conversation_id,
            OperatorTask.task_type == "handoff",
''',
    '''        .filter(
            OperatorTask.source_type == "webchat_handoff",
            OperatorTask.webchat_conversation_id == conversation_id,
            OperatorTask.task_type == "handoff",
''',
)
replace_once(
    AGENT,
    '''        offer.status = "cancelled"
        offer.cancelled_at = now
        offer.decline_reason = reason[:240]
        offer.updated_at = now
        affected_sessions.add(offer.voice_session_id)
''',
    '''        offer.status = "cancelled"
        offer.cancelled_at = now
        offer.decline_reason = reason[:240]
        offer.updated_at = now
        update_attempt_by_external_ref(
            db,
            external_ref=offer.public_id,
            outcome="cancelled",
            reason_code=reason,
        )
        affected_sessions.add(offer.voice_session_id)
''',
)
replace_once(
    AGENT,
    '''        offer.status = "expired"
        offer.expired_at = now
        offer.updated_at = now
        affected_sessions.add(offer.voice_session_id)
''',
    '''        offer.status = "expired"
        offer.expired_at = now
        offer.updated_at = now
        update_attempt_by_external_ref(
            db,
            external_ref=offer.public_id,
            outcome="expired",
            reason_code="voice_offer_expired",
        )
        affected_sessions.add(offer.voice_session_id)
''',
)

replace_once(
    AGENT,
    '''def _eligible_voice_agents(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    control: ConversationControl,
) -> list[tuple[User, OperatorAgentState]]:
    if not control.country_code:
        return []
    candidates = (
        db.query(User, OperatorAgentState)
        .join(
            OperatorAgentState,
            OperatorAgentState.user_id == User.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == User.id,
                OperatorQueueScopeGrant.tenant_key
                == control.tenant_key,
                OperatorQueueScopeGrant.country_code
                == control.country_code,
                OperatorQueueScopeGrant.channel_key
                == control.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .filter(
            User.is_active.is_(True),
            OperatorAgentState.status == "online",
            OperatorAgentState.voice_enabled.is_(True),
        )
        .order_by(
            OperatorAgentState.updated_at.asc(),
            User.id.asc(),
        )
        .all()
    )
    eligible: list[tuple[User, OperatorAgentState]] = []
    for user, state in candidates:
        if not heartbeat_is_fresh(state):
            continue
        if _agent_has_prior_voice_offer(
            db,
            handoff_request_id=request_row.id,
            agent_id=user.id,
        ):
            continue
        occupied = active_voice_load(db, user_id=user.id)
        reserved = reserved_voice_offer_count(
            db,
            user_id=user.id,
        )
        if occupied + reserved >= state.max_concurrent_voice_calls:
            continue
        eligible.append((user, state))
    return eligible
''',
    '''def _eligible_voice_agents(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    control: ConversationControl,
) -> list[tuple[User, OperatorAgentState]]:
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    candidates = eligible_agents(
        db,
        plan=plan,
        control=control,
        channel_kind="voice",
        require_voice=True,
    )
    eligible: list[tuple[User, OperatorAgentState]] = []
    for user, state in candidates:
        occupied = active_voice_load(db, user_id=user.id)
        reserved = reserved_voice_offer_count(db, user_id=user.id)
        if occupied + reserved >= state.max_concurrent_voice_calls:
            continue
        eligible.append((user, state))
    return eligible
''',
)

replace_once(
    AGENT,
    '''    control = _control_for_conversation(db, conversation)
    candidates = _eligible_voice_agents(
        db,
        request_row=request_row,
        control=control,
    )
    if not candidates:
        return None
''',
    '''    control = _control_for_conversation(db, conversation)
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    candidates = _eligible_voice_agents(
        db,
        request_row=request_row,
        control=control,
    )
    if not candidates:
        routing_status = schedule_retry_or_exhaust(
            db,
            plan=plan,
            reason_code="no_eligible_voice_candidate",
        )
        _event(
            db,
            conversation=conversation,
            event_type="handoff.routing.waiting",
            payload={
                "handoff_request_id": request_row.id,
                "channel_kind": "voice",
                "routing_status": routing_status,
            },
        )
        return None
''',
)
replace_once(
    AGENT,
    '''    voice_session.status = "ringing"
    voice_session.ringing_at = voice_session.ringing_at or now
''',
    '''    record_candidate_attempt(
        db,
        plan=plan,
        request_id=request_row.id,
        agent_id=user.id,
        channel_kind="voice",
        outcome="offered",
        external_ref=offer.public_id,
    )
    voice_session.status = "ringing"
    voice_session.ringing_at = voice_session.ringing_at or now
''',
)

replace_once(
    AGENT,
    '''        .limit(1)
    )
    return _lock(query, db).first()


def assign_handoff_to_agent(
''',
    '''        .distinct()
        .limit(100)
    )
    rows = _lock(query, db).all()
    for request_row, conversation, control in rows:
        plan = ensure_handoff_routing_plan(db, request_row=request_row)
        if candidate_is_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind="text",
        ):
            return request_row, conversation, control
    return None


def assign_handoff_to_agent(
''',
)
replace_once(
    AGENT,
    '''    control = _control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(
            status_code=403,
            detail="agent_scope_not_authorized",
        )

    locked_request = _lock(
''',
    '''    control = _control_for_conversation(db, conversation)
    early_voice_session = _voice_session_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    channel_kind = (
        "voice"
        if early_voice_session is not None
        else "manual"
        if mode == "manual"
        else "text"
    )
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    if not candidate_is_authorized(
        db,
        plan=plan,
        control=control,
        user=user,
        channel_kind=channel_kind,
        exclude_attempted=False,
    ):
        raise HTTPException(
            status_code=403,
            detail="agent_scenario_route_not_authorized",
        )

    locked_request = _lock(
''',
)
replace_once(
    AGENT,
    '''    voice_session = _voice_session_for_conversation(
        db,
        conversation_id=locked_conversation.id,
    )
    now = utc_now()
''',
    '''    voice_session = early_voice_session
    now = utc_now()
''',
)
replace_once(
    AGENT,
    '''        if ticket is not None:
            ticket.assignee_id = user.id
            ticket.updated_at = now
    channel_kind = "voice" if voice_session is not None else "text"
''',
    '''        if ticket is not None:
            ticket.assignee_id = user.id
            ticket.status = TicketStatus.in_progress
            ticket.conversation_state = ConversationState.human_owned
            ticket.required_action = None
            ticket.updated_at = now
    record_candidate_attempt(
        db,
        plan=plan,
        request_id=locked_request.id,
        agent_id=user.id,
        channel_kind=channel_kind,
        outcome="accepted",
        external_ref=(locked_offer.public_id if locked_offer is not None else None),
    )
    mark_plan_assigned(db, plan=plan, agent_id=user.id)
''',
)
replace_once(
    AGENT,
    '''    offer.status = "declined"
    offer.declined_at = now
    offer.decline_reason = (note or reason_code)[:240]
    offer.updated_at = now
''',
    '''    offer.status = "declined"
    offer.declined_at = now
    offer.decline_reason = (note or reason_code)[:240]
    offer.updated_at = now
    update_attempt_by_external_ref(
        db,
        external_ref=offer.public_id,
        outcome="declined",
        reason_code=reason_code,
    )
''',
)
replace_once(
    AGENT,
    '''    db.add(row)
    db.flush()
    voice_session = _voice_session_for_conversation(
''',
    '''    db.add(row)
    db.flush()
    ensure_handoff_routing_plan(db, request_row=row)
    voice_session = _voice_session_for_conversation(
''',
)
replace_once(
    AGENT,
    '''        source_type="webchat",
        source_id=str(conversation.id),
''',
    '''        source_type="webchat_handoff",
        source_id=str(row.id),
''',
)
replace_once(
    AGENT,
    '''def _auto_assign_text_request(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    control: ConversationControl,
) -> None:
    candidates = (
        db.query(User, OperatorAgentState)
        .join(
            OperatorAgentState,
            OperatorAgentState.user_id == User.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == User.id,
                OperatorQueueScopeGrant.tenant_key
                == control.tenant_key,
                OperatorQueueScopeGrant.country_code
                == control.country_code,
                OperatorQueueScopeGrant.channel_key
                == control.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .filter(
            User.is_active.is_(True),
            OperatorAgentState.status == "online",
        )
        .order_by(
            OperatorAgentState.updated_at.asc(),
            User.id.asc(),
        )
        .all()
    )
    for user, state in candidates:
        if request_row.status != "requested":
            return
        if not heartbeat_is_fresh(state):
            continue
        if (
            active_agent_load(db, user_id=user.id)
            >= state.max_concurrent_conversations
        ):
            continue
        assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=user,
            mode="automatic",
        )
        return
''',
    '''def _auto_assign_text_request(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    control: ConversationControl,
) -> None:
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    candidates = eligible_agents(
        db,
        plan=plan,
        control=control,
        channel_kind="text",
    )
    for user, state in candidates:
        if request_row.status != "requested":
            return
        if active_agent_load(db, user_id=user.id) >= state.max_concurrent_conversations:
            continue
        assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=user,
            mode="automatic",
        )
        return
    schedule_retry_or_exhaust(
        db,
        plan=plan,
        reason_code="no_eligible_text_candidate",
    )
''',
)
replace_once(
    AGENT,
    '''        "voice_offer": (
            {
                "id": active_offer.public_id,
                "agent_id": active_offer.agent_id,
                "expires_at": active_offer.expires_at.isoformat(),
            }
            if active_offer is not None
            else None
        ),
    }
''',
    '''        "voice_offer": (
            {
                "id": active_offer.public_id,
                "agent_id": active_offer.agent_id,
                "expires_at": active_offer.expires_at.isoformat(),
            }
            if active_offer is not None
            else None
        ),
        "routing": routing_projection(db, request_id=request_row.id),
    }
''',
)
replace_once(
    AGENT,
    '''        db.query(VoiceRoutingOffer).filter(
            VoiceRoutingOffer.handoff_request_id == request_row.id,
            VoiceRoutingOffer.status == "offered",
        ).update(
''',
    '''        close_routing_plan(
            db,
            request_id=request_row.id,
            outcome_code=normalized,
        )
        db.query(VoiceRoutingOffer).filter(
            VoiceRoutingOffer.handoff_request_id == request_row.id,
            VoiceRoutingOffer.status == "offered",
        ).update(
''',
)

# Ticket-backed manual accept now consumes the same assignment command.
replace_once(
    FACADE,
    '''    if request_row.ticket_id is not None:
        return _core.accept_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            note=note,
        )
''',
    '''    if request_row.ticket_id is not None:
        _require_capability(
            db,
            current_user=current_user,
            capability=CAP_WEBCHAT_HANDOFF_ACCEPT,
            detail="webchat_handoff_accept_requires_capability",
        )
        conversation = db.get(WebchatConversation, request_row.conversation_id)
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="webchat handoff source is missing",
            )
        from .agent_routing_service import assign_handoff_to_agent

        assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=current_user,
            mode="manual",
        )
        if note:
            request_row.decision_note = _core._clip(note, _core.MAX_NOTE_CHARS)
        ticket = db.get(_core.Ticket, request_row.ticket_id)
        db.flush()
        return _core.serialize_handoff_request(
            db,
            request_row,
            current_user=current_user,
            conversation=conversation,
            ticket=ticket,
        )
''',
)

# Retire the last old Projection identity lookup and expose routing facts.
replace_once(
    CORE,
    '''            OperatorTask.source_type == "webchat",
            OperatorTask.webchat_conversation_id == conversation.id,
''',
    '''            OperatorTask.source_type == "webchat_handoff",
            OperatorTask.source_id == str(request_row.id),
            OperatorTask.webchat_conversation_id == conversation.id,
''',
)
replace_once(
    CORE,
    '''    payload: dict[str, Any] = {
''',
    '''    from .handoff_routing_authority import routing_projection

    payload: dict[str, Any] = {
''',
)
replace_once(
    CORE,
    '''        "takeover_mode": conversation.takeover_mode if conversation else None,
''',
    '''        "takeover_mode": conversation.takeover_mode if conversation else None,
        "routing": routing_projection(db, request_id=request_row.id),
''',
)
replace_once(
    CORE,
    '''    db.add(decision)
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
''',
    '''    db.add(decision)
    from .handoff_routing_authority import (
        ensure_handoff_routing_plan,
        record_candidate_attempt,
    )

    plan = ensure_handoff_routing_plan(db, request_row=row)
    record_candidate_attempt(
        db,
        plan=plan,
        request_id=row.id,
        agent_id=current_user.id,
        channel_kind="manual",
        outcome="declined",
        reason_code=decision.reason_code,
    )
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
''',
)
replace_once(
    CORE,
    '''    row.updated_at = now
    if ticket.assignee_id == current_user.id:
''',
    '''    row.updated_at = now
    from .handoff_routing_authority import schedule_retry_or_exhaust

    schedule_retry_or_exhaust(
        db,
        plan=__import__(
            "app.services.handoff_routing_authority",
            fromlist=["routing_plan_for_request"],
        ).routing_plan_for_request(db, request_id=row.id, lock=True),
        reason_code="handoff_released",
    )
    if ticket.assignee_id == current_user.id:
''',
)
replace_once(
    CORE,
    '''    row.updated_at = now
    ticket.required_action = None
''',
    '''    row.updated_at = now
    from .handoff_routing_authority import close_routing_plan

    close_routing_plan(db, request_id=row.id, outcome_code="resumed_ai")
    ticket.required_action = None
''',
)

# Keep all exact-head templates and focused gates aligned with the linear head.
for path in (
    "deploy/.env.controlled.example",
    "deploy/.env.controlled.local-postgres.example",
    "backend/tests/test_production_readiness_convergence.py",
):
    replace_all(path, "20260728_r5_scenario", "20260728_r6_routing")

replace_once(
    WORKFLOW,
    '''            backend/tests/test_audit_838_r5_handoff_authority.py \\
''',
    '''            backend/tests/test_audit_838_r5_handoff_authority.py \\
            backend/tests/test_audit_838_r6_scenario_routing.py \\
''',
)

print("ROUTING_WAVE_PATCH_APPLIED=true")
