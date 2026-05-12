from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix='/api/tickets', tags=['outbound-policy'])


@router.post('/{ticket_id}/outbound/send')
def direct_outbound_send_disabled(ticket_id: int):
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='direct_outbound_send_disabled_use_draft_approval',
    )
