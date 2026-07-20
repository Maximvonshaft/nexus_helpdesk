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
) -> dict:
    """Change one operator's capacity without mutating presence or heartbeat."""

    capacity = int(max_concurrent_conversations)
    if not 1 <= capacity <= MAX_AGENT_CAPACITY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_agent_capacity",
        )
    row = get_or_create_agent_state(
        db,
        user_id=target_user.id,
        lock=True,
    )
    old_value = read_agent_state(db, user_id=target_user.id)
    if row.max_concurrent_conversations == capacity:
        return {**old_value, "idempotent": True}

    row.max_concurrent_conversations = capacity
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
        new_value={
            **new_value,
            "target_user_id": target_user.id,
        },
    )
    if new_value.get("assignable") and new_value.get("available_capacity", 0) > 0:
        fill_agent_capacity(db, user=target_user)
        new_value = read_agent_state(db, user_id=target_user.id)
    return new_value
