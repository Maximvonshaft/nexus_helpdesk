from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..models_agent_routing import ConversationControl
from ..services.agent_routing_service import (
    assign_handoff_to_agent,
    availability_summary,
    close_conversation,
    heartbeat_agent,
    read_agent_state,
    set_agent_state,
)
from ..services.permissions import (
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    ensure_capability,
)
from ..unit_of_work import managed_session
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .deps import get_current_user


router = APIRouter(prefix="/api/operator", tags=["operator-agent-routing"])


class AgentStateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(min_length=2, max_length=24)
    max_concurrent_conversations: int | None = Field(default=None, ge=1, le=20)


class ConversationCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(min_length=2, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


def _ensure_agent_capability(user: User, db: Session) -> None:
    ensure_capability(user, CAP_WEBCHAT_HANDOFF_ACCEPT, db)


@router.get("/agent-state")
def get_agent_state(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    with managed_session(db):
        return read_agent_state(db, user_id=current_user.id)


@router.put("/agent-state")
def update_agent_state(
    payload: AgentStateUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    with managed_session(db):
        return set_agent_state(
            db,
            user=current_user,
            presence_status=payload.status,
            max_concurrent_conversations=payload.max_concurrent_conversations,
        )


@router.post("/agent-state/heartbeat")
def heartbeat_agent_state(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    with managed_session(db):
        return heartbeat_agent(db, user=current_user)


@router.get("/availability")
def get_operator_availability(
    tenant_key: str = Query(min_length=1, max_length=120),
    country_code: str | None = Query(default=None, min_length=2, max_length=16),
    channel_key: str = Query(min_length=1, max_length=120),
    handoff_request_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    request_row = (
        db.get(WebchatHandoffRequest, handoff_request_id)
        if handoff_request_id is not None
        else None
    )
    return availability_summary(
        db,
        tenant_key=tenant_key.strip(),
        country_code=(country_code or "").strip().upper() or None,
        channel_key=channel_key.strip(),
        request_row=request_row,
    )


@router.post("/handoffs/{request_id}/accept")
def accept_operator_handoff(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    with managed_session(db):
        request_row = db.get(WebchatHandoffRequest, request_id)
        if request_row is None:
            raise HTTPException(status_code=404, detail="handoff_not_found")
        conversation = db.get(WebchatConversation, request_row.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=409, detail="handoff_conversation_missing")
        return assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=current_user,
            mode="manual",
        )


@router.post("/conversations/{conversation_id}/close")
def close_operator_conversation(
    conversation_id: str,
    payload: ConversationCloseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    with managed_session(db):
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .first()
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == conversation.id)
            .first()
        )
        if control is None:
            raise HTTPException(status_code=409, detail="conversation_control_missing")
        return close_conversation(
            db,
            conversation=conversation,
            user=current_user,
            outcome=payload.outcome,
            note=payload.note,
        )
