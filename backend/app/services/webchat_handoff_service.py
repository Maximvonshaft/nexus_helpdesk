"""Canonical WebChat handoff authority.

Ticket-backed transitions continue to use the established core implementation.
Ticketless WebChat conversations use the same HandoffRequest, OperatorTask,
audit, capability, and queue-scope authorities without manufacturing a Ticket.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import User
from ..models_agent_routing import ConversationControl
from ..operator_models import OperatorQueueScopeGrant
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffDecision,
    WebchatHandoffRequest,
)
from . import webchat_handoff_service_core as _core
from .audit_service import log_admin_audit
from .permissions import (
    CAP_OUTBOUND_SEND,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    resolve_capabilities,
)
from .webchat_handoff_service_core import (
    ensure_can_reply_in_handoff,
    force_takeover_ticket,
    request_webchat_handoff,
    serialize_handoff_request,
)


_OPEN = {"requested", "accepted"}
_TERMINAL = {"closed", "cancelled", "expired", "resumed_ai"}


def _require_capability(
    db: Session,
    *,
    current_user: User,
    capability: str,
    detail: str,
) -> set[str]:
    capabilities = resolve_capabilities(current_user, db)
    if capability not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )
    return capabilities


def _scope_visible(
    db: Session,
    *,
    current_user: User,
    control: ConversationControl,
) -> bool:
    """Normal queue work always requires an explicit active scope grant."""

    if not control.country_code:
        return False
    return bool(
        db.query(OperatorQueueScopeGrant.id)
        .filter(
            OperatorQueueScopeGrant.user_id == current_user.id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code == control.country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )


def _ticketless_context(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    current_user: User,
) -> tuple[WebchatConversation, ConversationControl]:
    conversation = db.get(WebchatConversation, request_row.conversation_id)
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == request_row.conversation_id)
        .first()
    )
    if conversation is None or control is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff source is missing",
        )
    if not _scope_visible(
        db,
        current_user=current_user,
        control=control,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="conversation_scope_not_authorized",
        )
    return conversation, control


def _declined_by_current_user(
    db: Session,
    *,
    request_id: int,
    user_id: int,
) -> bool:
    return bool(
        db.query(WebchatHandoffDecision.id)
        .filter(
            WebchatHandoffDecision.request_id == request_id,
            WebchatHandoffDecision.actor_id == user_id,
            WebchatHandoffDecision.decision == "declined",
        )
        .first()
    )


def _ticketless_payload(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    current_user: User,
) -> dict:
    # Local import preserves the existing WebChat-service initialization order.
    from .agent_availability_service import queue_position

    payload = _core.serialize_handoff_request(
        db,
        request_row,
        current_user=current_user,
        conversation=conversation,
        ticket=None,
    )
    capabilities = resolve_capabilities(current_user, db)
    payload.update(
        {
            "ticket_no": None,
            "title": request_row.reason_text
            or request_row.reason_code
            or "WebChat human support",
            "queue_position": queue_position(db, request_row=request_row),
            "can_reply": bool(
                request_row.status == "accepted"
                and request_row.assigned_agent_id == current_user.id
                and conversation.active_agent_id == current_user.id
                and CAP_OUTBOUND_SEND in capabilities
            ),
        }
    )
    return payload


def _ticketless_queue_items(
    db: Session,
    *,
    current_user: User,
    view: str,
    include_declined: bool,
    limit: int,
) -> list[dict]:
    query = (
        db.query(WebchatHandoffRequest, WebchatConversation, ConversationControl)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .filter(WebchatHandoffRequest.ticket_id.is_(None))
    )
    if view == "mine":
        query = query.filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id == current_user.id,
        )
    elif view == "closed":
        query = query.filter(WebchatHandoffRequest.status.in_(_TERMINAL))
    else:
        query = query.filter(WebchatHandoffRequest.status == "requested")

    rows = (
        query.order_by(
            WebchatHandoffRequest.requested_at.asc(),
            WebchatHandoffRequest.id.asc(),
        )
        .limit(max(1, min(limit, 100)) * 2)
        .all()
    )
    items: list[dict] = []
    for request_row, conversation, control in rows:
        if len(items) >= limit:
            break
        if not _scope_visible(
            db,
            current_user=current_user,
            control=control,
        ):
            continue
        declined = _declined_by_current_user(
            db,
            request_id=request_row.id,
            user_id=current_user.id,
        )
        if view == "requested" and declined and not include_declined:
            continue
        items.append(
            _ticketless_payload(
                db,
                request_row=request_row,
                conversation=conversation,
                current_user=current_user,
            )
        )
    return items


def list_handoff_queue(
    db: Session,
    current_user: User,
    *,
    view: str = "requested",
    include_declined: bool = False,
    limit: int = 50,
) -> dict:
    safe_limit = max(1, min(int(limit or 50), 100))
    legacy = _core.list_handoff_queue(
        db,
        current_user,
        view=view,
        include_declined=include_declined,
        limit=safe_limit,
    )
    ticketless = _ticketless_queue_items(
        db,
        current_user=current_user,
        view=view,
        include_declined=include_declined,
        limit=safe_limit,
    )
    combined = [*(legacy.get("items") or []), *ticketless]
    combined.sort(
        key=lambda item: (
            str(item.get("requested_at") or ""),
            int(item.get("id") or 0),
        )
    )
    return {
        "items": combined[:safe_limit],
        "view": view,
        "permissions": legacy.get("permissions")
        or {
            "can_accept": False,
            "can_decline": False,
            "can_force_takeover": False,
            "can_release": False,
            "can_resume_ai": False,
        },
    }


def accept_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict:
    request_row = db.get(WebchatHandoffRequest, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webchat handoff request not found",
        )
    if request_row.ticket_id is not None:
        return _core.accept_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            note=note,
        )

    _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_ACCEPT,
        detail="webchat_handoff_accept_requires_capability",
    )
    conversation, _control = _ticketless_context(
        db,
        request_row=request_row,
        current_user=current_user,
    )
    if (
        request_row.status == "accepted"
        and request_row.assigned_agent_id == current_user.id
    ):
        return _ticketless_payload(
            db,
            request_row=request_row,
            conversation=conversation,
            current_user=current_user,
        )
    if request_row.status != "requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="handoff_not_waiting",
        )

    # Local import avoids a WebChat-service initialization cycle.
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
    db.flush()
    return _ticketless_payload(
        db,
        request_row=request_row,
        conversation=conversation,
        current_user=current_user,
    )


def decline_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    reason_code: str | None = None,
    note: str | None = None,
) -> dict:
    request_row = db.get(WebchatHandoffRequest, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webchat handoff request not found",
        )
    if request_row.ticket_id is not None:
        return _core.decline_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            reason_code=reason_code,
            note=note,
        )

    _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_DECLINE,
        detail="webchat_handoff_decline_requires_capability",
    )
    request_row = _core._request_by_id(db, request_id, lock=True)
    conversation, _control = _ticketless_context(
        db,
        request_row=request_row,
        current_user=current_user,
    )
    if request_row.status != "requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only requested handoffs can be declined",
        )
    if _declined_by_current_user(
        db,
        request_id=request_row.id,
        user_id=current_user.id,
    ):
        return _ticketless_payload(
            db,
            request_row=request_row,
            conversation=conversation,
            current_user=current_user,
        )

    decision = WebchatHandoffDecision(
        request_id=request_row.id,
        actor_id=current_user.id,
        decision="declined",
        reason_code=_core._clip(reason_code, 160) or "agent_skipped",
        note=_core._clip(note, _core.MAX_NOTE_CHARS),
        created_at=utc_now(),
    )
    db.add(decision)
    request_row.decision_note = decision.note
    request_row.updated_at = utc_now()
    _core._write_handoff_event(
        db,
        conversation=conversation,
        ticket=None,
        request_row=request_row,
        event_type="handoff.declined",
        actor_id=current_user.id,
        payload={"reason_code": decision.reason_code, "note": decision.note},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.declined",
        target_type="webchat_handoff_request",
        target_id=request_row.id,
        new_value={"reason_code": decision.reason_code, "note": decision.note},
    )
    db.flush()
    return _ticketless_payload(
        db,
        request_row=request_row,
        conversation=conversation,
        current_user=current_user,
    )


def release_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict:
    request_row = db.get(WebchatHandoffRequest, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webchat handoff request not found",
        )
    if request_row.ticket_id is not None:
        return _core.release_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            note=note,
        )

    capabilities = _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_RELEASE,
        detail="webchat_handoff_release_requires_capability",
    )
    request_row = _core._request_by_id(db, request_id, lock=True)
    conversation, _control = _ticketless_context(
        db,
        request_row=request_row,
        current_user=current_user,
    )
    if request_row.status != "accepted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only accepted handoffs can be released",
        )
    if (
        request_row.assigned_agent_id != current_user.id
        and CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER not in capabilities
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="webchat handoff is owned by another agent",
        )

    now = utc_now()
    old_value = {
        "status": request_row.status,
        "assigned_agent_id": request_row.assigned_agent_id,
    }
    request_row.status = "requested"
    request_row.assigned_agent_id = None
    request_row.accepted_by_user_id = None
    request_row.released_at = now
    request_row.decision_note = _core._clip(note, _core.MAX_NOTE_CHARS)
    request_row.lock_version += 1
    request_row.updated_at = now
    _core._sync_conversation_snapshot(
        conversation=conversation,
        request_row=request_row,
        status_value="requested",
        active_agent_id=None,
        ai_suspended=True,
        ai_suspended_by=current_user.id,
        ai_suspended_reason="handoff_released",
        takeover_mode=None,
    )
    _core._sync_operator_task(
        db,
        conversation=conversation,
        request_row=request_row,
        status_value="pending",
        actor_id=None,
    )
    _core._write_handoff_event(
        db,
        conversation=conversation,
        ticket=None,
        request_row=request_row,
        event_type="handoff.released",
        actor_id=current_user.id,
        payload={"note": request_row.decision_note},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.released",
        target_type="webchat_handoff_request",
        target_id=request_row.id,
        old_value=old_value,
        new_value={"status": request_row.status, "assigned_agent_id": None},
    )
    db.flush()
    return _ticketless_payload(
        db,
        request_row=request_row,
        conversation=conversation,
        current_user=current_user,
    )


def resume_ai_for_handoff(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict:
    request_row = db.get(WebchatHandoffRequest, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webchat handoff request not found",
        )
    if request_row.ticket_id is not None:
        return _core.resume_ai_for_handoff(
            db,
            request_id=request_id,
            current_user=current_user,
            note=note,
        )

    _require_capability(
        db,
        current_user=current_user,
        capability=CAP_WEBCHAT_HANDOFF_RESUME_AI,
        detail="webchat_handoff_resume_ai_requires_capability",
    )
    request_row = _core._request_by_id(db, request_id, lock=True)
    conversation, _control = _ticketless_context(
        db,
        request_row=request_row,
        current_user=current_user,
    )
    if request_row.status not in _OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff request is already terminal",
        )

    now = utc_now()
    previous_agent_id = request_row.assigned_agent_id
    old_value = {
        "status": request_row.status,
        "assigned_agent_id": previous_agent_id,
    }
    request_row.status = "resumed_ai"
    request_row.assigned_agent_id = None
    request_row.closed_at = now
    request_row.decision_note = _core._clip(note, _core.MAX_NOTE_CHARS)
    request_row.lock_version += 1
    request_row.updated_at = now
    _core._sync_conversation_snapshot(
        conversation=conversation,
        request_row=None,
        status_value="none",
        active_agent_id=None,
        ai_suspended=False,
        ai_suspended_by=None,
        ai_suspended_reason=None,
        takeover_mode=None,
    )
    _core._sync_operator_task(
        db,
        conversation=conversation,
        request_row=request_row,
        status_value="resolved",
        actor_id=current_user.id,
    )
    _core._write_handoff_event(
        db,
        conversation=conversation,
        ticket=None,
        request_row=request_row,
        event_type="ai.resumed",
        actor_id=current_user.id,
        payload={"note": request_row.decision_note},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.resume_ai",
        target_type="webchat_handoff_request",
        target_id=request_row.id,
        old_value=old_value,
        new_value={"status": request_row.status, "assigned_agent_id": None},
    )
    db.flush()

    if previous_agent_id is not None:
        previous_agent = db.get(User, previous_agent_id)
        if previous_agent is not None:
            # Local import preserves service initialization order.
            from .agent_routing_service import fill_agent_capacity

            fill_agent_capacity(db, user=previous_agent)

    return _ticketless_payload(
        db,
        request_row=request_row,
        conversation=conversation,
        current_user=current_user,
    )


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "accept_handoff_request",
    "decline_handoff_request",
    "ensure_can_reply_in_handoff",
    "force_takeover_ticket",
    "list_handoff_queue",
    "release_handoff_request",
    "request_webchat_handoff",
    "resume_ai_for_handoff",
    "serialize_handoff_request",
]
