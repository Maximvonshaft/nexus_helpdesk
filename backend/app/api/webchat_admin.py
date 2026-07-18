from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import (
    ensure_can_accept_webchat_handoff,
    ensure_can_decline_webchat_handoff,
    ensure_can_force_takeover_webchat,
    ensure_can_monitor_webchat_ai,
    ensure_can_release_webchat_handoff,
    ensure_can_resume_webchat_ai,
    ensure_can_send_outbound,
)
from ..services.support_memory_ledger import build_support_memory_ledger
from ..services.support_sensitive_access import (
    audit_sensitive_support_read,
    ensure_sensitive_support_capability,
)
from ..services.webchat_handoff_service import (
    accept_handoff_request,
    decline_handoff_request,
    force_takeover_ticket,
    list_handoff_queue,
    release_handoff_request,
    resume_ai_for_handoff,
)
from ..services.webchat_inbox_read_state import mark_webchat_read_state
from ..services.webchat_service import admin_get_thread, admin_reply
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter()


class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)
    evidence_reference_id: int | None = Field(default=None, ge=1)


class WebchatHandoffDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=1000)


class WebchatHandoffTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


class WebchatReadStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marked_unread: bool = False


@router.get("/admin/conversations")
def list_webchat_conversations(
    current_user=Depends(get_current_user),
) -> None:
    del current_user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_webchat_conversation_list_retired",
            "canonical_endpoint": "/api/support/conversations",
        },
    )


@router.get("/admin/handoff/queue")
def get_webchat_handoff_queue(
    view: str = Query(
        default="requested",
        pattern="^(requested|ai_active|mine|closed)$",
    ),
    include_declined: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    if view == "ai_active":
        ensure_can_monitor_webchat_ai(current_user, db)
    else:
        ensure_can_accept_webchat_handoff(current_user, db)
    return list_handoff_queue(
        db,
        current_user,
        view=view,
        include_declined=include_declined,
        limit=limit,
    )


@router.post("/admin/handoff/{request_id}/accept")
def accept_webchat_handoff(
    request_id: int,
    payload: WebchatHandoffTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_accept_webchat_handoff(current_user, db)
    with managed_session(db):
        return accept_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            note=payload.note if payload else None,
        )


@router.post("/admin/handoff/{request_id}/decline")
def decline_webchat_handoff(
    request_id: int,
    payload: WebchatHandoffDecisionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_decline_webchat_handoff(current_user, db)
    with managed_session(db):
        return decline_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            reason_code=payload.reason_code if payload else None,
            note=payload.note if payload else None,
        )


@router.post("/admin/tickets/{ticket_id}/force-takeover")
def force_takeover_webchat(
    ticket_id: int,
    payload: WebchatHandoffDecisionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_force_takeover_webchat(current_user, db)
    with managed_session(db):
        return force_takeover_ticket(
            db,
            ticket_id=ticket_id,
            current_user=current_user,
            reason_code=payload.reason_code if payload else None,
            note=payload.note if payload else None,
        )


@router.post("/admin/handoff/{request_id}/release")
def release_webchat_handoff(
    request_id: int,
    payload: WebchatHandoffTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_release_webchat_handoff(current_user, db)
    with managed_session(db):
        return release_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            note=payload.note if payload else None,
        )


@router.post("/admin/handoff/{request_id}/resume-ai")
def resume_webchat_ai(
    request_id: int,
    payload: WebchatHandoffTransitionRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_resume_webchat_ai(current_user, db)
    with managed_session(db):
        return resume_ai_for_handoff(
            db,
            request_id=request_id,
            current_user=current_user,
            note=payload.note if payload else None,
        )


@router.get("/admin/tickets/{ticket_id}/thread")
def get_webchat_thread(
    ticket_id: int,
    before_message_id: int | None = Query(default=None, ge=1),
    message_limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_sensitive_support_capability(db, current_user)
    try:
        result = admin_get_thread(
            db,
            ticket_id,
            current_user,
            before_message_id=before_message_id,
            message_limit=message_limit,
        )
        if before_message_id is None:
            memory = build_support_memory_ledger(
                db,
                ticket_id=ticket_id,
                current_user=current_user,
            )
            result["support_memory"] = memory
            ai_state = memory.get("ai_state")
            if isinstance(ai_state, dict):
                result.update(ai_state)
    except HTTPException as exc:
        if exc.status_code in {
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        }:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="support_conversation_not_found",
            ) from exc
        raise
    audit_sensitive_support_read(
        current_user=current_user,
        ticket_id=ticket_id,
        includes_support_memory=before_message_id is None,
    )
    return result


@router.post("/admin/tickets/{ticket_id}/read-state")
def update_webchat_read_state(
    ticket_id: int,
    payload: WebchatReadStateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    with managed_session(db):
        return mark_webchat_read_state(
            db,
            ticket_id=ticket_id,
            current_user=current_user,
            marked_unread=payload.marked_unread,
        )


@router.post("/admin/tickets/{ticket_id}/reply")
def reply_webchat(
    ticket_id: int,
    payload: WebchatReplyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_send_outbound(current_user, db)
    with managed_session(db):
        return admin_reply(
            db,
            ticket_id,
            current_user,
            body=payload.body,
            evidence_reference_id=payload.evidence_reference_id,
        )
