from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.permissions import ensure_can_manage_runtime
from ..services.webcall_ai.demo_lab import (
    create_demo_session,
    end_demo_session,
    get_demo_lab_status,
    list_demo_events,
    process_demo_turn,
)
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/webcall-ai-demo", tags=["admin-webcall-ai-demo"])


class DemoSessionCreateRequest(BaseModel):
    locale: str | None = Field(default=None, max_length=20)
    display_name: str | None = Field(default=None, max_length=120)
    scenario: str | None = Field(default=None, max_length=120)
    initial_text: str | None = Field(default=None, max_length=1000)


class DemoTurnRequest(BaseModel):
    client_turn_id: str = Field(min_length=1, max_length=120)
    input_mode: str = Field(default="typed", max_length=40)
    locale: str | None = Field(default=None, max_length=20)
    text: str = Field(min_length=1)
    browser_speech_supported: bool | None = None


class DemoEndRequest(BaseModel):
    reason: str | None = Field(default="operator_end", max_length=80)


@router.get("/status")
def webcall_ai_demo_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    return get_demo_lab_status(db, current_user)


@router.post("/sessions")
def create_webcall_ai_demo_session(
    payload: DemoSessionCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        result = create_demo_session(db, current_user, payload)
        if payload.initial_text:
            process_demo_turn(
                db,
                current_user,
                result["session"]["public_id"],
                DemoTurnRequest(client_turn_id="initial", input_mode="typed", locale=payload.locale, text=payload.initial_text),
            )
        return result


@router.post("/sessions/{voice_session_public_id}/turns")
def create_webcall_ai_demo_turn(
    voice_session_public_id: str,
    payload: DemoTurnRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        return process_demo_turn(db, current_user, voice_session_public_id, payload)


@router.post("/sessions/{voice_session_public_id}/end")
def end_webcall_ai_demo_session(
    voice_session_public_id: str,
    payload: DemoEndRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    with managed_session(db):
        return end_demo_session(db, current_user, voice_session_public_id, payload or DemoEndRequest())


@router.get("/sessions/{voice_session_public_id}/events")
def webcall_ai_demo_events(
    voice_session_public_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    return list_demo_events(db, current_user, voice_session_public_id)
