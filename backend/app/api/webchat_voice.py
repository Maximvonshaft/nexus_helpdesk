from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..unit_of_work import managed_session
from ..voice_schemas import SpeedafVoiceCallbackRequest, SpeedafVoiceCallbackResponse, WebchatVoiceActionList, WebchatVoiceActionRequest, WebchatVoiceActionResponse, WebchatVoiceCreateRequest, WebchatVoiceEvidenceResponse, WebchatVoiceNoteRequest, WebchatVoiceNoteResponse, WebchatVoiceRejectRequest
from ..webchat_voice_config import load_webchat_voice_runtime_config
from ..services.webchat_voice_service import (
    DETAIL_EXPIRED,
    accept_admin_voice_session,
    create_public_voice_session,
    end_admin_voice_session,
    list_admin_incoming_voice_sessions,
    list_admin_voice_actions,
    list_admin_voice_evidence,
    end_public_voice_session,
    list_admin_voice_sessions,
    queue_speedaf_voice_callback,
    record_admin_voice_action,
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


@router.get("/admin/tickets/{ticket_id}/voice/{voice_session_id}/evidence", response_model=WebchatVoiceEvidenceResponse)
def read_ticket_voice_evidence(
    ticket_id: int,
    voice_session_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    return list_admin_voice_evidence(
        db,
        ticket_id=ticket_id,
        voice_session_public_id=voice_session_id,
        current_user=current_user,
        limit=limit,
    )


@router.get("/admin/tickets/{ticket_id}/voice/{voice_session_id}/actions", response_model=WebchatVoiceActionList)
def list_ticket_voice_actions(
    ticket_id: int,
    voice_session_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    return list_admin_voice_actions(
        db,
        ticket_id=ticket_id,
        voice_session_public_id=voice_session_id,
        current_user=current_user,
        limit=limit,
    )


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


@router.post("/admin/tickets/{ticket_id}/voice/{voice_session_id}/actions", response_model=WebchatVoiceActionResponse)
def create_ticket_voice_action(
    ticket_id: int,
    voice_session_id: str,
    payload: WebchatVoiceActionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return record_admin_voice_action(
            db,
            ticket_id=ticket_id,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
            action_type=payload.action_type,
            target=payload.target,
            digits=payload.digits,
            note=payload.note,
        )


@router.post("/admin/tickets/{ticket_id}/voice/{voice_session_id}/speedaf/callback", response_model=SpeedafVoiceCallbackResponse)
def queue_ticket_voice_speedaf_callback(
    ticket_id: int,
    voice_session_id: str,
    payload: SpeedafVoiceCallbackRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return queue_speedaf_voice_callback(
            db,
            ticket_id=ticket_id,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
            call_session_id=payload.callSessionId,
            is_transferred_to_human=payload.isTransferredToHuman,
            action=payload.action.model_dump(),
            request_id=getattr(request.state, "request_id", None),
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
        "enabled": config.human_call_enabled,
        "human_call_enabled": config.human_call_enabled,
        "live_ai_voice_enabled": config.live_ai_voice_enabled,
        "provider": config.provider,
        "livekit_url": config.livekit_url if config.provider == "livekit" else None,
        "recording_enabled": config.recording_enabled,
        "transcription_enabled": config.transcription_enabled,
    }
