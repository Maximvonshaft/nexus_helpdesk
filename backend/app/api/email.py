from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import EmailMailboxQueueResponse, EmailMailboxSyncEnqueueRequest, EmailMailboxSyncEnqueueResponse, EmailMailboxSyncStatusRead
from ..services.email_mailbox_queue_service import build_email_mailbox_queue
from ..services.email_mailbox_polling_service import build_email_mailbox_sync_status, enqueue_email_mailbox_sync_jobs
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(prefix="/api/email", tags=["email"])


@router.get("/queue", response_model=EmailMailboxQueueResponse)
def email_mailbox_queue(
    q: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return build_email_mailbox_queue(db, current_user, q=q, status_value=status, limit=limit)


@router.get("/mailbox-sync/status", response_model=EmailMailboxSyncStatusRead)
def email_mailbox_sync_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return build_email_mailbox_sync_status(db, current_user)


@router.post("/mailbox-sync/enqueue", response_model=EmailMailboxSyncEnqueueResponse)
def email_mailbox_sync_enqueue(
    payload: EmailMailboxSyncEnqueueRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    with managed_session(db):
        result = enqueue_email_mailbox_sync_jobs(db, current_user=current_user, account_id=payload.account_id)
        db.flush()
    return result
