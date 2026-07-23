from __future__ import annotations

import math
import statistics
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import User
from ..models_agent_routing import ConversationControl, OperatorAgentState
from ..operator_models import OperatorQueueScopeGrant
from ..utils.time import ensure_utc, utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .agent_routing_service import (
    active_agent_load,
    active_voice_load,
    heartbeat_is_fresh,
    reserved_voice_offer_count,
)

_OPEN_VOICE_STATUSES = ("created", "ringing", "accepted", "active")
_WAIT_SAMPLE_LIMIT = 200
_MIN_WAIT_SAMPLES = 5


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


def _conversation_requires_voice(
    db: Session,
    *,
    conversation_id: int | None,
    request_row: WebchatHandoffRequest | None,
) -> bool:
    resolved_id = conversation_id
    if resolved_id is None and request_row is not None:
        resolved_id = request_row.conversation_id
    if resolved_id is None:
        return False
    return bool(
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id == resolved_id,
            WebchatVoiceSession.status.in_(_OPEN_VOICE_STATUSES),
        )
        .first()
    )


def _scoped_queue_count(
    db: Session,
    *,
    tenant_key: str,
    country_code: str | None,
    channel_key: str,
    requires_voice: bool,
) -> int:
    query = (
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
    )
    if requires_voice:
        voice_exists = (
            db.query(WebchatVoiceSession.id)
            .filter(
                WebchatVoiceSession.conversation_id
                == WebchatHandoffRequest.conversation_id,
                WebchatVoiceSession.status.in_(_OPEN_VOICE_STATUSES),
            )
            .exists()
        )
        query = query.filter(voice_exists)
    return int(query.scalar() or 0)


def _recent_voice_service_seconds(
    db: Session,
    *,
    tenant_key: str,
    country_code: str | None,
    channel_key: str,
) -> list[int]:
    rows = (
        db.query(
            WebchatVoiceSession.accepted_at,
            WebchatVoiceSession.active_at,
            WebchatVoiceSession.ended_at,
        )
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatVoiceSession.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .filter(
            ConversationControl.tenant_key == tenant_key,
            ConversationControl.country_code == country_code,
            ConversationControl.channel_key == channel_key,
            WebchatVoiceSession.ended_at.isnot(None),
            WebchatVoiceSession.status == "ended",
        )
        .order_by(WebchatVoiceSession.ended_at.desc())
        .limit(_WAIT_SAMPLE_LIMIT)
        .all()
    )
    samples: list[int] = []
    for accepted_at, active_at, ended_at in rows:
        started = ensure_utc(accepted_at or active_at)
        ended = ensure_utc(ended_at)
        if started is None or ended is None or ended <= started:
            continue
        duration = int((ended - started).total_seconds())
        if 10 <= duration <= 4 * 60 * 60:
            samples.append(duration)
    return samples


def _percentile(samples: list[int], ratio: float) -> int:
    ordered = sorted(samples)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, math.ceil(ratio * len(ordered)) - 1))
    return int(ordered[index])


def _wait_estimate(
    *,
    requires_voice: bool,
    selected_available: int,
    selected_capacity: int,
    queue_count: int,
    position: int | None,
    samples: list[int],
) -> dict[str, Any]:
    if not requires_voice:
        return {
            "estimated_wait_seconds": None,
            "estimated_wait_range_seconds": None,
            "wait_estimate_confidence": "not_applicable",
            "wait_estimate_sample_size": 0,
            "wait_estimate_reason": "non_voice_request",
        }
    if selected_available > 0:
        return {
            "estimated_wait_seconds": 0,
            "estimated_wait_range_seconds": {"min": 0, "max": 0},
            "wait_estimate_confidence": "high",
            "wait_estimate_sample_size": len(samples),
            "wait_estimate_reason": "voice_capacity_available",
        }
    if selected_capacity <= 0:
        return {
            "estimated_wait_seconds": None,
            "estimated_wait_range_seconds": None,
            "wait_estimate_confidence": "unavailable",
            "wait_estimate_sample_size": len(samples),
            "wait_estimate_reason": "no_eligible_voice_capacity",
        }
    if len(samples) < _MIN_WAIT_SAMPLES:
        return {
            "estimated_wait_seconds": None,
            "estimated_wait_range_seconds": None,
            "wait_estimate_confidence": "insufficient_history",
            "wait_estimate_sample_size": len(samples),
            "wait_estimate_reason": "insufficient_completed_voice_sessions",
        }

    ahead = max(0, (position - 1) if position is not None else queue_count)
    waves = max(1, math.ceil((ahead + 1) / max(1, selected_capacity)))
    median_seconds = max(10, int(statistics.median(samples)))
    lower_seconds = max(10, _percentile(samples, 0.25))
    upper_seconds = max(median_seconds, _percentile(samples, 0.75))
    confidence = (
        "high" if len(samples) >= 30 else "medium" if len(samples) >= 10 else "low"
    )
    return {
        "estimated_wait_seconds": median_seconds * waves,
        "estimated_wait_range_seconds": {
            "min": lower_seconds * waves,
            "max": upper_seconds * waves,
        },
        "wait_estimate_confidence": confidence,
        "wait_estimate_sample_size": len(samples),
        "wait_estimate_reason": "recent_scoped_voice_service_time",
    }


def availability_summary(
    db: Session,
    *,
    tenant_key: str,
    country_code: str | None,
    channel_key: str,
    request_row: WebchatHandoffRequest | None = None,
    conversation_id: int | None = None,
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

    requires_voice = (
        str(channel_key or "").strip().lower() == "voice"
        or _conversation_requires_voice(
            db,
            conversation_id=conversation_id,
            request_row=request_row,
        )
    )
    queue_count = _scoped_queue_count(
        db,
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
        requires_voice=requires_voice,
    )
    position = (
        queue_position(db, request_row=request_row)
        if request_row is not None
        else None
    )
    available_capacity = max(0, total_capacity - occupied_capacity)
    available_voice_capacity = max(
        0,
        total_voice_capacity - occupied_voice_capacity - reserved_voice_capacity,
    )
    selected_available = available_voice_capacity if requires_voice else available_capacity
    selected_capacity = total_voice_capacity if requires_voice else total_capacity
    wait = _wait_estimate(
        requires_voice=requires_voice,
        selected_available=selected_available,
        selected_capacity=selected_capacity,
        queue_count=queue_count,
        position=position,
        samples=(
            _recent_voice_service_seconds(
                db,
                tenant_key=tenant_key,
                country_code=country_code,
                channel_key=channel_key,
            )
            if requires_voice
            else []
        ),
    )
    return {
        "checked_at": utc_now().isoformat(),
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
        "queue_position": position,
        **wait,
    }
