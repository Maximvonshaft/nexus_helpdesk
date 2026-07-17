from fastapi import APIRouter

from .webchat_admin import router as admin_router
from .webchat_public import router as public_router


router = APIRouter(prefix="/api/webchat", tags=["webchat"])
router.include_router(public_router)
router.include_router(admin_router)
