from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppMediaAsset
from ..services.conversation_operator_service import ensure_conversation_visible
from ..services.storage import get_storage_backend
from ..services.whatsapp_media_service import (
    WhatsAppMediaError,
    project_available_inbound_media_for_ticket,
)
from ..webchat_models import WebchatConversation
from .deps import get_current_user


router = APIRouter(
    prefix="/api/support/conversations",
    tags=["support-conversation-media"],
)


@router.get("/{conversation_public_id}/media/{asset_id}")
def download_conversation_whatsapp_media(
    conversation_public_id: str,
    asset_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    conversation = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == conversation_public_id)
        .first()
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation_not_found",
        )
    ensure_conversation_visible(
        db,
        conversation=conversation,
        user=current_user,
    )
    asset = (
        db.query(WhatsAppMediaAsset)
        .join(
            WhatsAppInboundMessage,
            WhatsAppInboundMessage.id == WhatsAppMediaAsset.inbound_message_id,
        )
        .filter(
            WhatsAppMediaAsset.id == asset_id,
            WhatsAppInboundMessage.conversation_id == conversation.id,
        )
        .first()
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation_media_not_found",
        )
    if (
        asset.storage_status != "available"
        or asset.scan_status != "clean"
        or not asset.storage_key
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conversation_media_not_available",
        )
    if conversation.ticket_id is not None:
        try:
            project_available_inbound_media_for_ticket(
                db,
                conversation_id=conversation.id,
                ticket_id=conversation.ticket_id,
            )
        except WhatsAppMediaError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.code,
            ) from exc
    storage = get_storage_backend()
    filename = asset.file_name or f"whatsapp-{asset.media_kind}.bin"
    media_type = (
        asset.detected_mime_type
        or asset.declared_mime_type
        or "application/octet-stream"
    )
    remote_url = storage.download_url(
        asset.storage_key,
        filename=filename,
        media_type=media_type,
    )
    if remote_url:
        return RedirectResponse(
            url=remote_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    path = storage.resolve(asset.storage_key)
    return FileResponse(
        path=path,
        media_type=media_type,
        filename=filename,
    )
