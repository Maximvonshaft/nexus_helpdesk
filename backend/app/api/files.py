from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.file_service import download_attachment_response, get_attachment_or_404
from ..services.permissions import ensure_attachment_accessible
from .deps import get_current_user

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/{attachment_id}/download")
def download_attachment(attachment_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    attachment = get_attachment_or_404(db, attachment_id)
    ensure_attachment_accessible(current_user, attachment, db)
    return download_attachment_response(attachment)
