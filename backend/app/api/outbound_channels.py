from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.outbound_channel_registry import list_outbound_channel_capabilities
from .deps import get_current_user

router = APIRouter(prefix="/api/outbound/channels", tags=["outbound-channels"])


@router.get("/capabilities")
def list_capabilities(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # Authentication is intentionally required because capability data includes
    # runtime readiness and missing operational prerequisites. The payload is
    # used by the agent reply UI to hide non-ready / unsafe channels.
    return {
        "channels": [item.to_dict() for item in list_outbound_channel_capabilities(db=db)],
    }
