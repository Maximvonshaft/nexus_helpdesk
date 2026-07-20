"""WebChat handoff public authority.

The private core continues to own legacy ticket-backed transitions. Ticketless
conversation handoffs are projected from the same WebchatHandoffRequest authority
and merged into the same public queue result.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import User
from ..models_agent_routing import ConversationControl
from ..operator_models import OperatorQueueScopeGrant
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffDecision,
    WebchatHandoffRequest,
)
from . import webchat_handoff_service_core as _core
from .agent_routing_service import serialize_handoff
from .permissions import (
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    has_global_case_visibility,
    resolve_capabilities,
)
from .webchat_handoff_service_core import (
    accept_handoff_request,
    decline_handoff_request,
    ensure_can_reply_in_handoff,
    force_takeover_ticket,
    release_handoff_request,
    request_webchat_handoff,
    resume_ai_for_handoff,
    serialize_handoff_request,
)


_OPEN = {"requested", "accepted"}
_TERMINAL = {"closed", "cancelled", "expired", "resumed_ai"}


def _scope_visible(
    db: Session,
    *,
    current_user: User,
    control: ConversationControl,
) -> bool:
    if has_global_case_visibility(current_user, db):
        return True
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
        payload = serialize_handoff(
            db,
            request_row=request_row,
            conversation=conversation,
        )
        payload.update(
            {
                "ticket_no": None,
                "title": request_row.reason_text
                or request_row.reason_code
                or "WebChat human support",
                "declined_by_me": declined,
                "visitor_name": conversation.visitor_name,
                "visitor_email": conversation.visitor_email,
                "visitor_phone": conversation.visitor_phone,
                "origin": conversation.origin,
                "can_accept": request_row.status == "requested"
                and CAP_WEBCHAT_HANDOFF_ACCEPT
                in resolve_capabilities(current_user, db),
                "can_decline": request_row.status == "requested"
                and CAP_WEBCHAT_HANDOFF_DECLINE
                in resolve_capabilities(current_user, db),
                "can_force_takeover": CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER
                in resolve_capabilities(current_user, db),
                "can_release": request_row.status == "accepted"
                and request_row.assigned_agent_id == current_user.id
                and CAP_WEBCHAT_HANDOFF_RELEASE
                in resolve_capabilities(current_user, db),
                "can_resume_ai": request_row.status in _OPEN
                and CAP_WEBCHAT_HANDOFF_RESUME_AI
                in resolve_capabilities(current_user, db),
                "can_reply": request_row.status == "accepted"
                and request_row.assigned_agent_id == current_user.id,
            }
        )
        items.append(payload)
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
