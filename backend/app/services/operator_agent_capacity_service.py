from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import User
from ..utils.time import utc_now
from .agent_routing_service import (
    MAX_AGENT_CAPACITY,
    fill_agent_capacity,
    get_or_create_agent_state,
    read_agent_state,
)
from .audit_service import log_admin_audit


def set_operator_agent_capacity(
    db: Session,
    *,
    actor: User,
    target_user: User,
    max_concurrent_conversations: int,
    max_concurrent_voice_calls: int = 1,
    voice_wrap_up_seconds: int = 30,
) -> dict:
    """Change one operator's text and voice capacity without mutating presence."""

    from .agent_routing_service import MAX_VOICE_CAPACITY, MAX_VOICE_WRAP_UP_SECONDS

    text_capacity = int(max_concurrent_conversations)
    voice_capacity = int(max_concurrent_voice_calls)
    wrap_up = int(voice_wrap_up_seconds)
    if not 1 <= text_capacity <= MAX_AGENT_CAPACITY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_agent_capacity")
    if not 1 <= voice_capacity <= MAX_VOICE_CAPACITY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_agent_voice_capacity")
    if not 0 <= wrap_up <= MAX_VOICE_WRAP_UP_SECONDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_agent_voice_wrap_up")
    row = get_or_create_agent_state(db, user_id=target_user.id, lock=True)
    old_value = read_agent_state(db, user_id=target_user.id)
    if (
        row.max_concurrent_conversations == text_capacity
        and row.max_concurrent_voice_calls == voice_capacity
        and row.voice_wrap_up_seconds == wrap_up
    ):
        return {**old_value, "idempotent": True}
    row.max_concurrent_conversations = text_capacity
    row.max_concurrent_voice_calls = voice_capacity
    row.voice_wrap_up_seconds = wrap_up
    row.updated_at = utc_now()
    db.flush()
    new_value = read_agent_state(db, user_id=target_user.id)
    log_admin_audit(
        db,
        actor_id=actor.id,
        action="operator_agent_capacity.updated",
        target_type="operator_agent_state",
        target_id=row.id,
        old_value=old_value,
        new_value={**new_value, "target_user_id": target_user.id},
    )
    if new_value.get("assignable") and (
        new_value.get("available_capacity", 0) > 0
        or new_value.get("available_voice_capacity", 0) > 0
    ):
        fill_agent_capacity(db, user=target_user)
        new_value = read_agent_state(db, user_id=target_user.id)
    return new_value
