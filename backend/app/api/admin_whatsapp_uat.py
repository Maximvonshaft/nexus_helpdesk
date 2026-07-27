from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import ensure_can_manage_channel_accounts
from ..services.whatsapp_connection_service import (
    WhatsAppConnectionError,
    get_whatsapp_connection,
)
from ..services.whatsapp_uat_evidence import (
    WhatsAppUatEvidenceError,
    WhatsAppUatSelection,
    collect_whatsapp_uat_facts,
)
from .deps import get_current_user


router = APIRouter(
    prefix="/api/admin/whatsapp/connections",
    tags=["admin-whatsapp-uat"],
)


@router.get("/{connection_id}/uat-evidence")
def get_whatsapp_uat_evidence(
    connection_id: int,
    inbound_provider_message_id: str = Query(min_length=1, max_length=255),
    outbound_provider_message_id: str = Query(min_length=1, max_length=255),
    media_inbound_provider_message_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=255,
    ),
    media_outbound_provider_message_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=255,
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    ensure_can_manage_channel_accounts(current_user, db)
    try:
        connection = get_whatsapp_connection(db, connection_id)
        return collect_whatsapp_uat_facts(
            db,
            connection=connection,
            selection=WhatsAppUatSelection(
                inbound_provider_message_id=inbound_provider_message_id,
                outbound_provider_message_id=outbound_provider_message_id,
                media_inbound_provider_message_id=(
                    media_inbound_provider_message_id
                ),
                media_outbound_provider_message_id=(
                    media_outbound_provider_message_id
                ),
            ),
        )
    except WhatsAppConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": str(exc), "retryable": False},
        ) from exc
    except WhatsAppUatEvidenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": str(exc), "retryable": False},
        ) from exc
