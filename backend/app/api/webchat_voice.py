from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..unit_of_work import managed_session
from ..voice_schemas import WebchatVoiceCreateRequest, WebchatVoiceNoteRequest, WebchatVoiceNoteResponse, WebchatVoiceRejectRequest
from ..webchat_voice_config import load_webchat_voice_runtime_config
from ..services.webchat_voice_service import (
    DETAIL_EXPIRED,
    accept_admin_voice_session,
    create_public_voice_session,
    end_admin_voice_session,
    list_admin_incoming_voice_sessions,
    end_public_voice_session,
    list_admin_voice_sessions,
    reject_admin_voice_session,
    save_admin_voice_note,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/webchat", tags=["webchat-voice"])


def _require_visitor_token(header_token: str | None) -> str:
    if not header_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    return header_token


@router.post("/conversations/{conversation_id}/voice/sessions")
def create_voice_session(
    conversation_id: str,
    payload: WebchatVoiceCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
) -> dict:
    visitor_token = _require_visitor_token(x_webchat_visitor_token)
    with managed_session(db):
        return create_public_voice_session(
            db,
            conversation_public_id=conversation_id,
            visitor_token=visitor_token,
            request=request,
            locale=payload.locale,
            recording_consent=payload.recording_consent,
        )


@router.post("/conversations/{conversation_id}/voice/{voice_session_id}/end")
def end_visitor_voice_session(
    conversation_id: str,
    voice_session_id: str,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
) -> dict:
    visitor_token = _require_visitor_token(x_webchat_visitor_token)
    with managed_session(db):
        return end_public_voice_session(
            db,
            conversation_public_id=conversation_id,
            voice_session_public_id=voice_session_id,
            visitor_token=visitor_token,
        )


@router.get("/admin/tickets/{ticket_id}/voice/sessions")
def list_ticket_voice_sessions(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    return list_admin_voice_sessions(db, ticket_id=ticket_id, current_user=current_user)


@router.get("/admin/voice/sessions")
def list_incoming_voice_sessions(
    status: str = "ringing",
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return list_admin_incoming_voice_sessions(db, current_user=current_user, status_filter=status, limit=limit)


@router.post("/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept")
def accept_ticket_voice_session(
    ticket_id: int,
    voice_session_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    try:
        result = accept_admin_voice_session(
            db,
            ticket_id=ticket_id,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
        )
        db.commit()
        return result
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT and exc.detail == DETAIL_EXPIRED:
            db.commit()
        else:
            db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/admin/tickets/{ticket_id}/voice/{voice_session_id}/reject")
def reject_ticket_voice_session(
    ticket_id: int,
    voice_session_id: str,
    payload: WebchatVoiceRejectRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return reject_admin_voice_session(
            db,
            ticket_id=ticket_id,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
            reason=payload.reason if payload else None,
        )


@router.post("/admin/tickets/{ticket_id}/voice/{voice_session_id}/notes", response_model=WebchatVoiceNoteResponse)
def save_ticket_voice_note(
    ticket_id: int,
    voice_session_id: str,
    payload: WebchatVoiceNoteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return save_admin_voice_note(
            db,
            ticket_id=ticket_id,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
            body=payload.body,
            source=payload.source,
        )


@router.post("/admin/tickets/{ticket_id}/voice/{voice_session_id}/end")
def end_ticket_voice_session(
    ticket_id: int,
    voice_session_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return end_admin_voice_session(db, ticket_id=ticket_id, voice_session_public_id=voice_session_id, current_user=current_user)


@router.get("/voice/runtime-config")
def voice_runtime_config() -> dict:
    config = load_webchat_voice_runtime_config()
    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "livekit_url": config.livekit_url if config.provider == "livekit" else None,
        "recording_enabled": config.recording_enabled,
        "transcription_enabled": config.transcription_enabled,
    }
