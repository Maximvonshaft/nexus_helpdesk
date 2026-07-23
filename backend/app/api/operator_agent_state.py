from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..models_agent_routing import ConversationControl
from ..services.agent_availability_service import availability_summary
from ..services.agent_routing_service import (
    assign_handoff_to_agent,
    close_conversation,
    heartbeat_agent,
    read_agent_state,
    set_agent_state,
)
from ..services.conversation_operator_service import (
    read_conversation_thread,
    reply_to_conversation,
)
from ..services.operator_agent_capacity_service import set_operator_agent_capacity
from ..services.operator_queue_scope import authorize_operator_scope
from ..services.permissions import (
    CAP_USER_MANAGE,
    CAP_WEBCALL_VOICE_ACCEPT,
    CAP_WEBCALL_VOICE_CONTROL,
    CAP_WEBCALL_VOICE_END,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCALL_VOICE_READ,
    CAP_WEBCALL_VOICE_REJECT,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    ensure_capability,
    resolve_capabilities,
)
from ..unit_of_work import managed_session
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .deps import get_current_user

router = APIRouter(prefix="/api/operator", tags=["operator-agent-routing"])

_VOICE_OPT_IN_CAPABILITIES = frozenset(
    {
        CAP_WEBCALL_VOICE_READ,
        CAP_WEBCALL_VOICE_QUEUE_VIEW,
        CAP_WEBCALL_VOICE_ACCEPT,
        CAP_WEBCALL_VOICE_REJECT,
        CAP_WEBCALL_VOICE_END,
        CAP_WEBCALL_VOICE_CONTROL,
    }
)


class AgentStateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(min_length=2, max_length=24)
    max_concurrent_conversations: int | None = Field(default=None, ge=1, le=20)
    voice_enabled: bool | None = None
    max_concurrent_voice_calls: int | None = Field(default=None, ge=1, le=5)
    voice_wrap_up_seconds: int | None = Field(default=None, ge=0, le=900)


class AgentCapacityUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_concurrent_conversations: int = Field(ge=1, le=20)
    voice_enabled: bool = False
    max_concurrent_voice_calls: int = Field(default=1, ge=1, le=5)
    voice_wrap_up_seconds: int = Field(default=30, ge=0, le=900)


class ConversationReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


class ConversationCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(min_length=2, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


def _ensure_agent_capability(user: User, db: Session) -> None:
    ensure_capability(user, CAP_WEBCHAT_HANDOFF_ACCEPT, db)


def _ensure_agent_state_capability(user: User, db: Session) -> set[str]:
    capabilities = resolve_capabilities(user, db)
    if (
        CAP_WEBCHAT_HANDOFF_ACCEPT not in capabilities
        and CAP_WEBCALL_VOICE_QUEUE_VIEW not in capabilities
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_agent_state_requires_capability",
        )
    return capabilities


def _ensure_voice_opt_in_capability(capabilities: set[str]) -> None:
    missing = sorted(_VOICE_OPT_IN_CAPABILITIES - capabilities)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_voice_opt_in_requires_complete_capability_bundle",
        )


def _managed_operator(db: Session, *, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="operator_not_found",
        )
    capabilities = resolve_capabilities(target, db)
    if (
        CAP_WEBCHAT_HANDOFF_ACCEPT not in capabilities
        and CAP_WEBCALL_VOICE_QUEUE_VIEW not in capabilities
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="target_user_is_not_operator",
        )
    return target


def _conversation_by_public_id(
    db: Session,
    *,
    conversation_id: str,
) -> WebchatConversation:
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == conversation_id)
        .first()
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation_not_found",
        )
    return conversation


@router.get("/agent-state")
def get_agent_state(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_state_capability(current_user, db)
    with managed_session(db):
        return read_agent_state(db, user_id=current_user.id)


@router.put("/agent-state")
def update_agent_state(
    payload: AgentStateUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    capabilities = _ensure_agent_state_capability(current_user, db)
    with managed_session(db):
        current_state = read_agent_state(db, user_id=current_user.id)
        governed_capacity_fields = {
            "max_concurrent_conversations": payload.max_concurrent_conversations,
            "max_concurrent_voice_calls": payload.max_concurrent_voice_calls,
            "voice_wrap_up_seconds": payload.voice_wrap_up_seconds,
        }
        capacity_changed = any(
            value is not None and value != current_state.get(name)
            for name, value in governed_capacity_fields.items()
        )
        if capacity_changed and CAP_USER_MANAGE not in capabilities:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="agent_capacity_update_requires_user_manage",
            )
        voice_opt_in_requested = (
            payload.voice_enabled is True
            and payload.voice_enabled != current_state.get("voice_enabled")
        )
        if voice_opt_in_requested:
            _ensure_voice_opt_in_capability(capabilities)
        return set_agent_state(
            db,
            user=current_user,
            presence_status=payload.status,
            max_concurrent_conversations=payload.max_concurrent_conversations,
            voice_enabled=payload.voice_enabled,
            max_concurrent_voice_calls=payload.max_concurrent_voice_calls,
            voice_wrap_up_seconds=payload.voice_wrap_up_seconds,
        )


@router.post("/agent-state/heartbeat")
def heartbeat_agent_state(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_state_capability(current_user, db)
    with managed_session(db):
        return heartbeat_agent(db, user=current_user)


@router.get("/agent-states/{user_id}")
def get_managed_agent_state(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_capability(current_user, CAP_USER_MANAGE, db)
    with managed_session(db):
        target = _managed_operator(db, user_id=user_id)
        return {
            **read_agent_state(db, user_id=target.id),
            "username": target.username,
            "display_name": target.display_name,
            "is_active": target.is_active,
        }


@router.put("/agent-states/{user_id}/capacity")
def update_managed_agent_capacity(
    user_id: int,
    payload: AgentCapacityUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_capability(current_user, CAP_USER_MANAGE, db)
    with managed_session(db):
        target = _managed_operator(db, user_id=user_id)
        return set_operator_agent_capacity(
            db,
            actor=current_user,
            target_user=target,
            max_concurrent_conversations=payload.max_concurrent_conversations,
            voice_enabled=payload.voice_enabled,
            max_concurrent_voice_calls=payload.max_concurrent_voice_calls,
            voice_wrap_up_seconds=payload.voice_wrap_up_seconds,
        )


@router.get("/availability")
def get_operator_availability(
    tenant_key: str = Query(min_length=1, max_length=80),
    country_code: str = Query(min_length=2, max_length=16),
    channel_key: str = Query(min_length=1, max_length=40),
    handoff_request_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    tenant, country, channel, _grant = authorize_operator_scope(
        db,
        current_user=current_user,
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
    )
    request_row = (
        db.get(WebchatHandoffRequest, handoff_request_id)
        if handoff_request_id is not None
        else None
    )
    if request_row is not None:
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == request_row.conversation_id)
            .first()
        )
        if control is None or (
            control.tenant_key,
            control.country_code,
            control.channel_key,
        ) != (tenant, country, channel):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="handoff_not_found_in_scope",
            )
    return availability_summary(
        db,
        tenant_key=tenant,
        country_code=country,
        channel_key=channel,
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="handoff_not_found",
            )
        conversation = db.get(WebchatConversation, request_row.conversation_id)
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="handoff_conversation_missing",
            )
        return assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=current_user,
            mode="manual",
        )


@router.get("/conversations/{conversation_id}/thread")
def get_operator_conversation_thread(
    conversation_id: str,
    before_message_id: int | None = Query(default=None, ge=1),
    message_limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    conversation = _conversation_by_public_id(db, conversation_id=conversation_id)
    return read_conversation_thread(
        db,
        conversation=conversation,
        user=current_user,
        before_message_id=before_message_id,
        message_limit=message_limit,
    )


@router.post("/conversations/{conversation_id}/reply")
def reply_operator_conversation(
    conversation_id: str,
    payload: ConversationReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_agent_capability(current_user, db)
    with managed_session(db):
        conversation = _conversation_by_public_id(db, conversation_id=conversation_id)
        return reply_to_conversation(
            db,
            conversation=conversation,
            user=current_user,
            body=payload.body,
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
        conversation = _conversation_by_public_id(db, conversation_id=conversation_id)
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == conversation.id)
            .first()
        )
        if control is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="conversation_control_missing",
            )
        return close_conversation(
            db,
            conversation=conversation,
            user=current_user,
            outcome=payload.outcome,
            note=payload.note,
        )
