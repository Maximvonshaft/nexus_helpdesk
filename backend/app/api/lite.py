from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..models import Team, User
from ..schemas import (
    LiteAIIntakeRequest,
    LiteAssignRequest,
    LiteCaseCreate,
    LiteCaseDetail,
    LiteCaseListItem,
    LiteCaseUpdate,
    LiteHumanNoteRequest,
    LiteMetaRead,
    LiteStatusRequest,
    LiteWorkflowUpdateRequest,
    TeamRead,
    UserRead,
)
from ..services.lite_service import (
    LITE_STATUS_ORDER,
    assign_lite_case,
    change_lite_status,
    create_lite_case,
    get_lite_case,
    list_lite_cases,
    save_ai_intake_lite,
    save_human_note_lite,
    update_lite_case,
    workflow_update_lite_case,
)
from .deps import get_current_user
from ..unit_of_work import managed_session

router = APIRouter(prefix="/api/lite", tags=["lite"])


@router.get("/stream")
def lite_stream(current_user=Depends(get_current_user)):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="Realtime stream is disabled; use polling /api/lite/cases instead")


@router.get("/meta", response_model=LiteMetaRead)
def get_lite_meta(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        users = db.query(User).filter(User.is_active.is_(True)).order_by(User.display_name.asc()).all()
        teams = db.query(Team).filter(Team.is_active.is_(True)).order_by(Team.name.asc()).all()
    else:
        users = db.query(User).filter(User.is_active.is_(True), User.team_id == current_user.team_id).order_by(User.display_name.asc()).all()
        if current_user.id and all(user.id != current_user.id for user in users):
            users.append(current_user)
        teams = db.query(Team).filter(Team.is_active.is_(True), Team.id == current_user.team_id).order_by(Team.name.asc()).all()
    return LiteMetaRead(
        users=[UserRead.model_validate(x) for x in users],
        teams=[TeamRead.model_validate(x) for x in teams],
        statuses=LITE_STATUS_ORDER,
        priorities=["low", "medium", "high", "urgent"],
    )


@router.get("/cases", response_model=list[LiteCaseListItem])
def list_cases(
    q: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_lite_cases(db, current_user, q=q, status=status)


@router.get("/cases/{ticket_id}", response_model=LiteCaseDetail)
def get_case(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return get_lite_case(db, ticket_id, current_user)


@router.post("/cases")
def create_case(payload: LiteCaseCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        case, action = create_lite_case(db, payload, current_user)
        db.flush()
    return {"action": action, "case": case}


@router.patch("/cases/{ticket_id}", response_model=LiteCaseDetail)
def update_case(ticket_id: int, payload: LiteCaseUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        case = update_lite_case(db, ticket_id, payload, current_user)
        db.flush()
    return case


@router.post("/cases/{ticket_id}/workflow-update", response_model=LiteCaseDetail)
def workflow_update_case(ticket_id: int, payload: LiteWorkflowUpdateRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        case = workflow_update_lite_case(db, ticket_id, payload, current_user)
        db.flush()
    if payload.status == "resolved" and case.status == "resolved":
        from ..services.auto_reply_service import fire_and_forget_auto_reply
        fire_and_forget_auto_reply(case.id, current_user.id)
    return case


@router.post("/cases/{ticket_id}/assign", response_model=LiteCaseDetail)
def assign_case(ticket_id: int, payload: LiteAssignRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        case = assign_lite_case(db, ticket_id, payload, current_user)
        db.flush()
    return case


@router.post("/cases/{ticket_id}/status", response_model=LiteCaseDetail)
def set_status(ticket_id: int, payload: LiteStatusRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        case = change_lite_status(db, ticket_id, payload, current_user)
        db.flush()
    if payload.status == "resolved" and case.status == "resolved":
        from ..services.auto_reply_service import fire_and_forget_auto_reply
        fire_and_forget_auto_reply(case.id, current_user.id)
    return case


@router.post("/cases/{ticket_id}/human-note", response_model=LiteCaseDetail)
def save_human_note(ticket_id: int, payload: LiteHumanNoteRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        case = save_human_note_lite(db, ticket_id, payload, current_user)
        db.flush()
    if case.status == "resolved":
        from ..services.auto_reply_service import fire_and_forget_auto_reply
        fire_and_forget_auto_reply(case.id, current_user.id)
    return case


@router.post("/cases/{ticket_id}/ai-intake", response_model=LiteCaseDetail)
def save_ai_intake(ticket_id: int, payload: LiteAIIntakeRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    with managed_session(db):
        case = save_ai_intake_lite(db, ticket_id, payload, current_user)
        db.flush()
    return case
