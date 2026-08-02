from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.webchat_rate_limit import enforce_webchat_preauth_rate_limit


def enforce_webchat_conversation_preauth(
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    enforce_webchat_preauth_rate_limit(db, request)


__all__ = ["enforce_webchat_conversation_preauth"]
