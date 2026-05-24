from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..webcall_ai_schemas import WebCallAIEndRequest, WebCallAIHandoffRequest, WebCallAISessionCreateRequest, WebCallAITrackingFallbackRequest
from ..services.webcall_ai_production.config import get_webcall_ai_production_settings
from ..services.webcall_ai_production.session_service import (
    WebCallAIInitPayload,
    create_join_token,
    create_session,
    end_session,
    get_session,
    list_events,
    request_handoff,
    save_tracking_fallback,
)
from ..unit_of_work import managed_session

router = APIRouter(prefix="/api/webcall-ai", tags=["webcall-ai"])


@router.get("/runtime-config")
def runtime_config() -> dict[str, object]:
    return get_webcall_ai_production_settings().public_runtime_config()


@router.post("/sessions")
def create_webcall_ai_session(
    payload: WebCallAISessionCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    with managed_session(db):
        return create_session(
            db,
            request=request,
            payload=WebCallAIInitPayload(**payload.model_dump()),
            idempotency_key=idempotency_key,
        )


@router.get("/sessions/{session_public_id}")
def read_webcall_ai_session(
    session_public_id: str,
    db: Session = Depends(get_db),
    x_webcall_ai_visitor_token: str | None = Header(default=None, alias="X-WebCall-AI-Visitor-Token"),
) -> dict:
    return get_session(db, session_public_id, x_webcall_ai_visitor_token)


@router.post("/sessions/{session_public_id}/join-token")
def webcall_ai_join_token(
    session_public_id: str,
    db: Session = Depends(get_db),
    x_webcall_ai_visitor_token: str | None = Header(default=None, alias="X-WebCall-AI-Visitor-Token"),
) -> dict:
    return create_join_token(db, session_public_id, x_webcall_ai_visitor_token)


@router.post("/sessions/{session_public_id}/end")
def end_webcall_ai_session(session_public_id: str, payload: WebCallAIEndRequest, db: Session = Depends(get_db)) -> dict:
    with managed_session(db):
        return end_session(db, session_public_id, payload.visitor_token)


@router.post("/sessions/{session_public_id}/handoff")
def handoff_webcall_ai_session(session_public_id: str, payload: WebCallAIHandoffRequest, db: Session = Depends(get_db)) -> dict:
    with managed_session(db):
        return request_handoff(db, session_public_id, payload.visitor_token, payload.reason)


@router.post("/sessions/{session_public_id}/tracking-fallback")
def tracking_fallback_webcall_ai_session(session_public_id: str, payload: WebCallAITrackingFallbackRequest, db: Session = Depends(get_db)) -> dict:
    with managed_session(db):
        return save_tracking_fallback(db, session_public_id, payload.visitor_token, payload.tracking_number)


@router.get("/sessions/{session_public_id}/events")
def webcall_ai_events(
    session_public_id: str,
    db: Session = Depends(get_db),
    x_webcall_ai_visitor_token: str | None = Header(default=None, alias="X-WebCall-AI-Visitor-Token"),
) -> dict:
    return list_events(db, session_public_id, x_webcall_ai_visitor_token)
