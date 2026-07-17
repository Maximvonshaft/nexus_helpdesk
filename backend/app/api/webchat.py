from fastapi import APIRouter

from .webchat_admin import (
    WebchatHandoffDecisionRequest,
    WebchatHandoffTransitionRequest,
    WebchatReadStateRequest,
    WebchatReplyRequest,
    accept_webchat_handoff,
    decline_webchat_handoff,
    force_takeover_webchat,
    get_webchat_handoff_queue,
    get_webchat_thread,
    list_webchat_conversations,
    release_webchat_handoff,
    reply_webchat,
    resume_webchat_ai,
    router as admin_router,
    update_webchat_read_state,
)
from .webchat_public import (
    WebchatInitRequest,
    WebchatSendRequest,
    init_webchat,
    poll_webchat_messages,
    router as public_router,
    send_webchat_message,
    submit_webchat_action,
    webchat_options,
)

router = APIRouter(prefix="/api/webchat", tags=["webchat"])
router.include_router(public_router)
router.include_router(admin_router)

__all__ = [
    "router",
    "WebchatInitRequest",
    "WebchatSendRequest",
    "WebchatReplyRequest",
    "WebchatHandoffDecisionRequest",
    "WebchatHandoffTransitionRequest",
    "WebchatReadStateRequest",
    "webchat_options",
    "init_webchat",
    "send_webchat_message",
    "poll_webchat_messages",
    "submit_webchat_action",
    "list_webchat_conversations",
    "get_webchat_handoff_queue",
    "accept_webchat_handoff",
    "decline_webchat_handoff",
    "force_takeover_webchat",
    "release_webchat_handoff",
    "resume_webchat_ai",
    "get_webchat_thread",
    "update_webchat_read_state",
    "reply_webchat",
]
