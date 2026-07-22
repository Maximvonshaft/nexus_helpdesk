from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..unit_of_work import managed_session
from ..voice_schemas import (
    SpeedafVoiceCallbackRequest,
    SpeedafVoiceCallbackResponse,
    WebchatVoiceActionList,
    WebchatVoiceActionRequest,
    WebchatVoiceActionResponse,
    WebchatVoiceCreateRequest,
    WebchatVoiceEvidenceResponse,
    WebchatVoiceNoteRequest,
    WebchatVoiceNoteResponse,
    WebchatVoiceRejectRequest,
)
from ..webchat_voice_config import load_webchat_voice_runtime_config
from ..services.observability import record_voice_provider_error
from ..services.voice_business_action_service import queue_speedaf_voice_callback
from ..services.voice_evidence_service import (
    list_admin_voice_actions,
    list_admin_voice_evidence,
    record_admin_voice_action,
    save_admin_voice_note,
)
from ..services.voice_provider import VoiceProviderError
from ..services.voice_session_service import (
    DETAIL_EXPIRED,
    accept_admin_voice_session,
    create_public_voice_session,
    end_admin_voice_session,
    end_public_voice_session,
    list_admin_incoming_voice_sessions,
    list_admin_voice_sessions,
    load_voice_session,
    reject_admin_voice_session,
    serialize_voice_session,
)
from .deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webchat", tags=["webchat-voice"])


def _require_visitor_token(header_token: str | None) -> str:
    if not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid webchat visitor token",
        )
    return header_token


@router.post("/conversations/{conversation_id}/voice/sessions")
def create_voice_session(
    conversation_id: str,
    payload: WebchatVoiceCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(
        default=None,
        alias="X-Webchat-Visitor-Token",
    ),
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
    x_webchat_visitor_token: str | None = Header(
        default=None,
        alias="X-Webchat-Visitor-Token",
    ),
) -> dict:
    visitor_token = _require_visitor_token(x_webchat_visitor_token)
    with managed_session(db):
        try:
            return end_public_voice_session(
                db,
                conversation_public_id=conversation_id,
                voice_session_public_id=voice_session_id,
                visitor_token=visitor_token,
            )
        except VoiceProviderError:
            session = load_voice_session(db, voice_session_id)
            if session.conversation_id is None or session.ended_at is None:
                raise
            record_voice_provider_error(session.provider, "close_room")
            logger.warning(
                "voice_room_cleanup_deferred",
                extra={
                    "voice_session_id": session.public_id,
                    "provider": session.provider,
                },
            )
            return serialize_voice_session(db, session=session)


@router.get("/admin/tickets/{ticket_id}/voice/sessions")
def list_ticket_voice_sessions(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    return list_admin_voice_sessions(
        db,
        ticket_id=ticket_id,
        current_user=current_user,
    )


@router.get("/admin/voice/sessions")
def list_incoming_voice_sessions(
    status: str = "ringing",
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return list_admin_incoming_voice_sessions(
            db,
            current_user=current_user,
            status_filter=status,
            limit=limit,
        )


@router.get(
    "/admin/voice/{voice_session_id}/evidence",
    response_model=WebchatVoiceEvidenceResponse,
)
def read_voice_evidence(
    voice_session_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    return list_admin_voice_evidence(
        db,
        voice_session_public_id=voice_session_id,
        current_user=current_user,
        limit=limit,
    )


@router.get(
    "/admin/voice/{voice_session_id}/actions",
    response_model=WebchatVoiceActionList,
)
def read_voice_actions(
    voice_session_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    return list_admin_voice_actions(
        db,
        voice_session_public_id=voice_session_id,
        current_user=current_user,
        limit=limit,
    )


@router.post("/admin/voice/{voice_session_id}/accept")
def accept_voice_session(
    voice_session_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    try:
        result = accept_admin_voice_session(
            db,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
        )
        db.commit()
        return result
    except HTTPException as exc:
        if (
            exc.status_code == status.HTTP_409_CONFLICT
            and exc.detail == DETAIL_EXPIRED
        ):
            db.commit()
        else:
            db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception(
            "voice_accept_failed",
            extra={"actor_user_id": getattr(current_user, "id", None)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="voice session acceptance is temporarily unavailable",
        ) from None


@router.post("/admin/voice/{voice_session_id}/reject")
def reject_voice_session(
    voice_session_id: str,
    payload: WebchatVoiceRejectRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return reject_admin_voice_session(
            db,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
            reason=payload.reason if payload else None,
        )


@router.post(
    "/admin/voice/{voice_session_id}/notes",
    response_model=WebchatVoiceNoteResponse,
)
def save_voice_note(
    voice_session_id: str,
    payload: WebchatVoiceNoteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return save_admin_voice_note(
            db,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
            body=payload.body,
            source=payload.source,
        )


@router.post(
    "/admin/voice/{voice_session_id}/actions",
    response_model=WebchatVoiceActionResponse,
)
def create_voice_action(
    voice_session_id: str,
    payload: WebchatVoiceActionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    try:
        with managed_session(db):
            return record_admin_voice_action(
                db,
                voice_session_public_id=voice_session_id,
                current_user=current_user,
                action_type=payload.action_type,
                target=payload.target,
                digits=payload.digits,
                note=payload.note,
                idempotency_key=payload.idempotency_key,
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "voice_command_request_failed",
            extra={"actor_user_id": getattr(current_user, "id", None)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="voice provider command is temporarily unavailable",
        ) from None


@router.post(
    "/admin/voice/{voice_session_id}/speedaf/callback",
    response_model=SpeedafVoiceCallbackResponse,
)
def queue_voice_speedaf_callback(
    voice_session_id: str,
    payload: SpeedafVoiceCallbackRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return queue_speedaf_voice_callback(
            db,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
            call_session_id=payload.callSessionId,
            is_transferred_to_human=payload.isTransferredToHuman,
            action=payload.action.model_dump(),
            request_id=getattr(request.state, "request_id", None),
        )


@router.post("/admin/voice/{voice_session_id}/end")
def end_voice_session(
    voice_session_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    with managed_session(db):
        return end_admin_voice_session(
            db,
            voice_session_public_id=voice_session_id,
            current_user=current_user,
        )


@router.get("/voice/runtime-config")
def voice_runtime_config() -> dict:
    config = load_webchat_voice_runtime_config()
    return {
        "enabled": config.human_call_enabled,
        "human_call_enabled": config.human_call_enabled,
        "live_ai_voice_enabled": config.live_ai_voice_enabled,
        "provider": config.provider,
        "routing_mode": config.routing_mode,
        "media_plane": (
            "livekit" if config.provider == "livekit" else "mock"
        ),
        "livekit_url": (
            config.livekit_url if config.provider == "livekit" else None
        ),
        "recording_enabled": config.recording_enabled,
        "transcription_enabled": config.transcription_enabled,
    }
