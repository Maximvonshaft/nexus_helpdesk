from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas_qa_training import QAQueueRead, QAReviewCreate, QAReviewRead, QATrainingTaskRead
from ..services.permissions import ensure_can_manage_qa_training, ensure_can_read_qa_training
from ..services.qa_training_service import create_qa_review, list_qa_queue, list_training_tasks
from ..unit_of_work import managed_session
from .deps import get_current_user


router = APIRouter(prefix="/api/admin/qa-training", tags=["qa-training"])


@router.get("/queue", response_model=QAQueueRead)
def get_qa_training_queue(
    channel: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_can_read_qa_training(current_user, db)
    return list_qa_queue(db, channel=channel, status_filter=status, limit=limit)


@router.get("/training-tasks", response_model=list[QATrainingTaskRead])
def get_qa_training_tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_can_read_qa_training(current_user, db)
    return list_training_tasks(db, status_filter=status, limit=limit)


@router.post("/reviews", response_model=QAReviewRead)
def post_qa_review(
    payload: QAReviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_can_manage_qa_training(current_user, db)
    with managed_session(db):
        return create_qa_review(db, payload, reviewer=current_user)
