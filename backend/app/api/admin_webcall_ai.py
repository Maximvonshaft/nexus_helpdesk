from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import ensure_ticket_visible
from ..services.webcall_ai_production.session_service import TERMINAL_STATUSES, get_session, list_events
from ..services.webchat_voice_service import end_admin_voice_session
from ..unit_of_work import managed_session
from ..voice_models import WebchatVoiceSession
from ..models import Ticket
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/webcall-ai", tags=["admin-webcall-ai"])


@router.get("/sessions")
def list_admin_webcall_ai_sessions(
    status: str = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    query = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.mode == "livekit_ai_agent")
    if status == "active":
        query = query.filter(WebchatVoiceSession.status.notin_(list(TERMINAL_STATUSES)))
    elif status != "all":
        query = query.filter(WebchatVoiceSession.status == status)
    items = []
    for session in query.order_by(WebchatVoiceSession.id.desc()).limit(limit * 3).all():
        ticket = db.query(Ticket).filter(Ticket.id == session.ticket_id).first()
        if ticket is None:
            continue
        ensure_ticket_visible(current_user, ticket, db)
        items.append(get_session(db, session.public_id, require_visitor_token=False)["session"])
        if len(items) >= limit:
            break
    return {"items": items}


@router.get("/sessions/{session_public_id}")
def read_admin_webcall_ai_session(session_public_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict:
    result = get_session(db, session_public_id, require_visitor_token=False)
    ticket = db.query(Ticket).filter(Ticket.id == result["session"]["ticket_id"]).first()
    if ticket is not None:
        ensure_ticket_visible(current_user, ticket, db)
    return result


@router.get("/sessions/{session_public_id}/events")
def read_admin_webcall_ai_events(session_public_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict:
    result = list_events(db, session_public_id, require_visitor_token=False)
    ticket = db.query(Ticket).filter(Ticket.id == result["session"]["ticket_id"]).first()
    if ticket is not None:
        ensure_ticket_visible(current_user, ticket, db)
    return result


@router.post("/sessions/{session_public_id}/force-end")
def force_end_admin_webcall_ai_session(session_public_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict:
    result = get_session(db, session_public_id, require_visitor_token=False)
    ticket = db.query(Ticket).filter(Ticket.id == result["session"]["ticket_id"]).first()
    if ticket is not None:
        ensure_ticket_visible(current_user, ticket, db)
    with managed_session(db):
        return end_admin_voice_session(db, ticket_id=result["session"]["ticket_id"], voice_session_public_id=session_public_id, current_user=current_user)
