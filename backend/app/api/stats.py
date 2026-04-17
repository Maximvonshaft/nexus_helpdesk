from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import TicketStatsRead
from ..services.ticket_service import get_ticket_stats
from .deps import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/tickets", response_model=TicketStatsRead)
def ticket_stats(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return get_ticket_stats(db, current_user)
