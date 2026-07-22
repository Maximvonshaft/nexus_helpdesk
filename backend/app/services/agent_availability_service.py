from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import User
from ..models_agent_routing import ConversationControl, OperatorAgentState
from ..operator_models import OperatorQueueScopeGrant
from ..voice_models import WebchatVoiceSession
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .agent_routing_service import (
    active_agent_load,
    active_voice_load,
    heartbeat_is_fresh,
    reserved_voice_offer_count,
)


def _request_scope(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> ConversationControl | None:
    return (
        db.query(ConversationControl)
        .join(
            WebchatConversation,
            WebchatConversation.id == ConversationControl.conversation_id,
        )
        .filter(WebchatConversation.id == request_row.conversation_id)
        .first()
    )


def queue_position(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> int | None:
    if request_row.status != "requested":
        return None
    control = _request_scope(db, request_row=request_row)
    if control is None:
        return None
    return int(
        db.query(func.count(WebchatHandoffRequest.id))
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .filter(
            WebchatHandoffRequest.status == "requested",
            ConversationControl.tenant_key == control.tenant_key,
            ConversationControl.country_code == control.country_code,
            ConversationControl.channel_key == control.channel_key,
            (
                (WebchatHandoffRequest.requested_at < request_row.requested_at)
                | (
                    (WebchatHandoffRequest.requested_at == request_row.requested_at)
                    & (WebchatHandoffRequest.id <= request_row.id)
                )
            ),
        )
        .scalar()
        or 0
    )


def _agent_authorized_for_scope(
    db: Session,
    *,
    user: User,
    tenant_key: str,
    country_code: str | None,
    channel_key: str,
) -> bool:
    if not country_code:
        return False
    return bool(
        db.query(OperatorQueueScopeGrant.id)
        .filter(
            OperatorQueueScopeGrant.user_id == user.id,
            OperatorQueueScopeGrant.tenant_key == tenant_key,
            OperatorQueueScopeGrant.country_code == country_code,
            OperatorQueueScopeGrant.channel_key == channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )


def availability_summary(
    db: Session,
    *,
    tenant_key: str,
    country_code: str | None,
    channel_key: str,
    request_row: WebchatHandoffRequest | None = None,
) -> dict[str, Any]:
    candidates = (
        db.query(User, OperatorAgentState)
        .join(OperatorAgentState, OperatorAgentState.user_id == User.id)
        .filter(User.is_active.is_(True), OperatorAgentState.status == "online")
        .all()
    )
    online_agents = 0
    voice_enabled_agents = 0
    total_capacity = 0
    occupied_capacity = 0
    total_voice_capacity = 0
    occupied_voice_capacity = 0
    reserved_voice_capacity = 0
    for user, state in candidates:
        if not heartbeat_is_fresh(state):
            continue
        if not _agent_authorized_for_scope(
            db,
            user=user,
            tenant_key=tenant_key,
            country_code=country_code,
            channel_key=channel_key,
        ):
            continue
        online_agents += 1
        total_capacity += state.max_concurrent_conversations
        occupied_capacity += min(
            state.max_concurrent_conversations,
            active_agent_load(db, user_id=user.id),
        )
        if state.voice_enabled:
            voice_enabled_agents += 1
            total_voice_capacity += state.max_concurrent_voice_calls
            occupied_voice_capacity += min(
                state.max_concurrent_voice_calls,
                active_voice_load(db, user_id=user.id),
            )
            reserved_voice_capacity += min(
                state.max_concurrent_voice_calls,
                reserved_voice_offer_count(db, user_id=user.id),
            )

    queue_count = int(
        db.query(func.count(WebchatHandoffRequest.id))
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .filter(
            WebchatHandoffRequest.status == "requested",
            ConversationControl.tenant_key == tenant_key,
            ConversationControl.country_code == country_code,
            ConversationControl.channel_key == channel_key,
        )
        .scalar()
        or 0
    )
    requires_voice = False
    if request_row is not None:
        requires_voice = bool(
            db.query(WebchatVoiceSession.id)
            .filter(
                WebchatVoiceSession.conversation_id == request_row.conversation_id,
                WebchatVoiceSession.status.in_(["created", "ringing", "accepted", "active"]),
            )
            .first()
        )
    available_capacity = max(0, total_capacity - occupied_capacity)
    available_voice_capacity = max(
        0,
        total_voice_capacity - occupied_voice_capacity - reserved_voice_capacity,
    )
    selected_available = available_voice_capacity if requires_voice else available_capacity
    return {
        "available": selected_available > 0,
        "requires_voice_capacity": requires_voice,
        "online_agents": online_agents,
        "voice_enabled_agents": voice_enabled_agents,
        "total_capacity": total_capacity,
        "occupied_capacity": occupied_capacity,
        "available_capacity": available_capacity,
        "total_voice_capacity": total_voice_capacity,
        "occupied_voice_capacity": occupied_voice_capacity,
        "reserved_voice_capacity": reserved_voice_capacity,
        "available_voice_capacity": available_voice_capacity,
        "queue_count": queue_count,
        "queue_position": (
            queue_position(db, request_row=request_row)
            if request_row is not None
            else None
        ),
    }
