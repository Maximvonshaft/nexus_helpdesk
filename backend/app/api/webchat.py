from typing import Any

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..models import BackgroundJob
from ..services.background_jobs import WEBCHAT_AI_REPLY_JOB, enqueue_background_job
from ..services.conversation_first_service import create_or_resume_conversation
from ..services.webchat_ai_turn_service import ai_snapshot, schedule_webchat_ai_turn
from ..settings import get_settings
from ..webchat_models import WebchatConversation, WebchatMessage
from . import webchat_public
from .webchat_admin import router as admin_router


settings = get_settings()
_original_schedule_ai_turn_for_result = webchat_public._schedule_ai_turn_for_result


def _conversation_first_schedule_ai_turn_for_result(
    db: Session,
    *,
    conversation: WebchatConversation,
    result: dict[str, Any],
) -> dict[str, Any]:
    if conversation.ticket_id is not None:
        return _original_schedule_ai_turn_for_result(
            db,
            conversation=conversation,
            result=result,
        )
    message_payload = result.get("message") if isinstance(result, dict) else None
    message_id = message_payload.get("id") if isinstance(message_payload, dict) else None
    if not message_id or result.get("idempotent"):
        result.update(ai_snapshot(conversation))
        return result
    visitor_message = (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.id == int(message_id),
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "visitor",
        )
        .first()
    )
    if visitor_message is None:
        result.update(ai_snapshot(conversation))
        return result

    def create_job(
        payload: dict[str, Any],
        dedupe_key: str,
        scheduled_at,
    ) -> BackgroundJob:
        worker_payload = dict(payload)
        worker_payload["ticket_id"] = 0
        return enqueue_background_job(
            db,
            queue_name="webchat_ai_reply",
            job_type=WEBCHAT_AI_REPLY_JOB,
            payload=worker_payload,
            dedupe_key=dedupe_key,
            next_run_at=scheduled_at,
        )

    result.update(
        schedule_webchat_ai_turn(
            db,
            conversation=conversation,
            ticket_id=None,
            visitor_message=visitor_message,
            create_job=create_job,
            debounce_seconds=float(
                getattr(settings, "webchat_ai_turn_debounce_seconds", 0.15) or 0
            ),
        )
    )
    return result


# Public endpoints resolve these module globals at request time. Replacing the
# two authorities here keeps one route and one worker queue.
webchat_public.create_or_resume_conversation = create_or_resume_conversation
webchat_public._schedule_ai_turn_for_result = _conversation_first_schedule_ai_turn_for_result
public_router = webchat_public.router

router = APIRouter(prefix="/api/webchat", tags=["webchat"])
router.include_router(public_router)
router.include_router(admin_router)
