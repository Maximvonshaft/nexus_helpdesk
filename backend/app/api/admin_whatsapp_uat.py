from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
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
    replay_whatsapp_uat_inbound,
)
from ..unit_of_work import managed_session
from .deps import get_current_user


router = APIRouter(
    prefix="/api/admin/whatsapp/connections",
    tags=["admin-whatsapp-uat"],
)


class WhatsAppUatReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_message_id: str = Field(min_length=1, max_length=255)

    @field_validator("provider_message_id", mode="before")
    @classmethod
    def strip_provider_message_id(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/{connection_id}/uat-replay-inbound")
def replay_whatsapp_uat_message(
    connection_id: int,
    payload: WhatsAppUatReplayRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    ensure_can_manage_channel_accounts(current_user, db)
    try:
        connection = get_whatsapp_connection(db, connection_id)
        with managed_session(db):
            result = replay_whatsapp_uat_inbound(
                db,
                connection=connection,
                provider_message_id=payload.provider_message_id,
            )
            db.flush()
        return result
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
