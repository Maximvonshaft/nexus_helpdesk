from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from .deps import get_current_user
from ..db import get_db

router = APIRouter(prefix='/api/webchat', tags=['webchat-formal-outbound-policy'])


class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: str = Field(min_length=1, max_length=2000)
    has_fact_evidence: bool = False
    confirm_review: bool = False


@router.post('/admin/tickets/{ticket_id}/reply')
def reply_webchat(ticket_id: int, payload: WebchatReplyRequest, db=Depends(get_db), current_user=Depends(get_current_user)):
    """Block manual Webchat final/formal replies.

    Webchat remains available for AI frontline service through the legacy public
    Webchat routes. Formal customer notifications must be drafted and approved
    from the Ticket workflow, then dispatched by Email or WhatsApp.
    """
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='webchat_formal_outbound_disabled')
