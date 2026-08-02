from fastapi import APIRouter, Depends

from .webchat_admin import router as admin_router
from .webchat_preauth import enforce_webchat_conversation_preauth
from .webchat_public import router as public_router


router = APIRouter(prefix="/api/webchat", tags=["webchat"])
router.include_router(
    public_router,
    dependencies=[Depends(enforce_webchat_conversation_preauth)],
)
router.include_router(admin_router)
