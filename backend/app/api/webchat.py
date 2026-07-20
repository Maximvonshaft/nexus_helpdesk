from fastapi import APIRouter

from ..services.conversation_first_service import create_or_resume_conversation
from . import webchat_public
from .webchat_admin import router as admin_router


# Keep one public router and replace only its initialization authority. Endpoint
# functions resolve this module global at call time, so no parallel route exists.
webchat_public.create_or_resume_conversation = create_or_resume_conversation
public_router = webchat_public.router

router = APIRouter(prefix="/api/webchat", tags=["webchat"])
router.include_router(public_router)
router.include_router(admin_router)
