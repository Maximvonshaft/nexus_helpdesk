from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import JobStatus, MessageStatus, SourceChannel
from ..models import BackgroundJob, Ticket
from ..models_agent_routing import ConversationControl
from ..services.background_jobs import WEBCHAT_AI_REPLY_JOB, enqueue_background_job
from ..services.conversation_first_service import create_or_resume_conversation
from ..services.customer_language import resolve_conversation_language
from ..services.customer_visible_message_service import create_customer_visible_message
from ..services.data_subject_action_service import (
    DataProcessingRestricted,
    ensure_data_processing_allowed,
)
from ..services.observability import log_event, record_webchat_websocket_fallback_polling
from ..services.webchat_ai_reconciler import reconcile_webchat_ai_state
from ..services.webchat_ai_turn_service import ai_snapshot, schedule_webchat_ai_turn
from ..services.webchat_handoff_service import request_webchat_handoff
from ..services.webchat_performance import list_public_messages_throttled, webchat_poll_interval_ms
from ..services.webchat_public_payload import parse_public_webchat_json, public_webchat_message_payload
from ..services.webchat_rate_limit import enforce_webchat_rate_limit
from ..services.webchat_origin_policy import public_cors_headers, set_public_cors
from ..services.webchat_service import (
    _validate_token as validate_webchat_visitor_token,
    add_visitor_message_to_conversation,
    submit_card_action_to_conversation,
)
from ..services.webchat_session_identity import origin_from_request
from ..settings import get_settings
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from ..webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage
from ..webchat_schemas import WebChatActionSubmitRequest

router = APIRouter()
settings = get_settings()


class WebchatInitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_key: str = Field(default="default", max_length=120)
    channel_key: str = Field(default="default", max_length=120)
    conversation_id: str | None = Field(default=None, max_length=64)
    visitor_token: str | None = Field(default=None, max_length=160)
    visitor_name: str | None = Field(default=None, max_length=160)
    visitor_email: str | None = Field(default=None, max_length=200)
    visitor_phone: str | None = Field(default=None, max_length=80)
    visitor_ref: str | None = Field(default=None, max_length=160)
    origin: str | None = Field(default=None, max_length=255)
    page_url: str | None = Field(default=None, max_length=700)


class WebchatSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)
    body: str = Field(min_length=1, max_length=2000)
    client_message_id: str | None = Field(default=None, max_length=120)


PUBLIC_CORS_METHODS = ("GET", "POST", "OPTIONS")
PUBLIC_CORS_REQUEST_HEADERS = (
    "Content-Type",
    "X-Requested-With",
    "X-Webchat-Visitor-Token",
    "X-Webchat-WS-Fallback",
)


def _public_cors_headers(request: Request) -> dict[str, str]:
    return public_cors_headers(
        request,
        settings,
        methods=PUBLIC_CORS_METHODS,
        request_headers=PUBLIC_CORS_REQUEST_HEADERS,
    )


def _set_public_cors(response: Response, request: Request) -> None:
    set_public_cors(
        response,
        request,
        settings,
        methods=PUBLIC_CORS_METHODS,
        request_headers=PUBLIC_CORS_REQUEST_HEADERS,
    )


def _legacy_token_transport_enabled() -> bool:
    if settings.app_env == "production":
        return False
    return os.getenv(
        "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_visitor_token(
    header_token: str | None,
    query_token: str | None,
    body_token: str | None = None,
) -> str | None:
    if header_token:
        return header_token
    if _legacy_token_transport_enabled():
        return body_token or query_token
    return None


def _attach_ai_snapshot(
    result: dict[str, Any],
    conversation: WebchatConversation,
) -> dict[str, Any]:
    result.update(ai_snapshot(conversation))
    return result


def _apply_webchat_config_defaults(
    result: dict[str, Any],
) -> dict[str, Any]:
    config = dict(result.get("config") or {})
    config["poll_interval_ms"] = webchat_poll_interval_ms()
    config.setdefault("supports_after_id", True)
    result["config"] = config
    return result


def _conversation_customer_id(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> tuple[int | None, Ticket | None]:
    ticket = db.get(Ticket, conversation.ticket_id) if conversation.ticket_id else None
    if ticket is not None:
        return ticket.customer_id, ticket
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    return (control.customer_id if control else None), None


def _restricted_handoff_copy(body: str) -> str:
    language = resolve_conversation_language(body).language
    messages = {
        "zh": "我已收到你的消息，正在为你转接人工客服。请稍候。",
        "de": "Ihre Nachricht ist eingegangen. Ich verbinde Sie mit unserem Support-Team.",
        "fr": "Votre message a bien été reçu. Je vous mets en relation avec notre équipe d’assistance.",
        "it": "Abbiamo ricevuto il tuo messaggio. Ti metto in contatto con il team di assistenza.",
        "pt": "Recebemos a sua mensagem. Vou encaminhar para a nossa equipa de suporte.",
    }
    return messages.get(
        language,
        "Your message has been received. I’m connecting you with our support team.",
    )


def _apply_processing_restriction(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    customer_id, ticket = _conversation_customer_id(db, conversation=conversation)
    try:
        ensure_data_processing_allowed(
            db,
            customer_id=customer_id,
            purpose="automated_ai",
        )
    except DataProcessingRestricted:
        request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="privacy_policy",
            trigger_type="processing_restricted",
            reason_code="automated_processing_restricted",
            reason_text="Automated processing is restricted for this customer.",
            recommended_agent_action="Continue with human support only.",
            trigger_message_id=visitor_message.id,
            requested_by_actor_type="system",
        )
        idempotency_key = f"privacy-handoff:{visitor_message.id}"
        existing = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.client_message_id == idempotency_key,
            )
            .first()
        )
        if existing is None:
            visible = create_customer_visible_message(
                db,
                ticket=ticket,
                conversation=conversation,
                channel=SourceChannel.web_chat,
                body=_restricted_handoff_copy(visitor_message.body or ""),
                origin="provider_runtime",
                created_by=None,
                provider_status="privacy_handoff_sent",
                outbound_status=MessageStatus.sent,
                delivery_status="sent",
                author_label="Support",
                create_external_comment=ticket is not None,
                event_payload={
                    "conversation_id": conversation.id,
                    "visitor_message_id": visitor_message.id,
                    "reason_code": "automated_processing_restricted",
                    "contains_privacy_detail": False,
                },
            )
            if visible.webchat_message is None:
                raise RuntimeError("privacy_handoff_customer_message_missing")
            visible.webchat_message.client_message_id = idempotency_key
        result.update(
            {
                "ai_pending": False,
                "ai_status": "cancelled",
                "handoff_triggered": True,
                "processing_restricted": True,
            }
        )
        return _attach_ai_snapshot(result, conversation)
    return None


def _schedule_ai_turn_for_result(
    db: Session,
    *,
    conversation: WebchatConversation,
    result: dict[str, Any],
) -> dict[str, Any]:
    message_payload = result.get("message") if isinstance(result, dict) else None
    message_id = message_payload.get("id") if isinstance(message_payload, dict) else None
    if not message_id or result.get("idempotent"):
        return _attach_ai_snapshot(result, conversation)

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
        return _attach_ai_snapshot(result, conversation)

    restricted = _apply_processing_restriction(
        db,
        conversation=conversation,
        visitor_message=visitor_message,
        result=result,
    )
    if restricted is not None:
        return restricted

    legacy_job = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.dedupe_key == f"webchat-ai-reply:{visitor_message.id}",
            BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing]),
        )
        .order_by(BackgroundJob.id.desc())
        .first()
    )
    if legacy_job is not None:
        legacy_job.status = JobStatus.done
        legacy_job.locked_at = None
        legacy_job.locked_by = None
        legacy_job.next_run_at = None
        legacy_job.last_error = None
        legacy_job.updated_at = utc_now()

    def create_job(
        payload: dict[str, Any],
        dedupe_key: str,
        scheduled_at,
    ) -> BackgroundJob:
        return enqueue_background_job(
            db,
            queue_name="webchat_ai_reply",
            job_type=WEBCHAT_AI_REPLY_JOB,
            payload=payload,
            dedupe_key=dedupe_key,
            next_run_at=scheduled_at,
        )

    result.update(
        schedule_webchat_ai_turn(
            db,
            conversation=conversation,
            ticket_id=conversation.ticket_id,
            visitor_message=visitor_message,
            create_job=create_job,
            debounce_seconds=float(
                getattr(settings, "webchat_ai_turn_debounce_seconds", 0.15) or 0
            ),
        )
    )
    return result


def _find_existing_action_response(
    db: Session,
    *,
    conversation: WebchatConversation,
    payload: WebChatActionSubmitRequest,
) -> dict[str, Any] | None:
    candidates = (
        db.query(WebchatCardAction)
        .filter(
            WebchatCardAction.conversation_id == conversation.id,
            WebchatCardAction.message_id == payload.message_id,
            WebchatCardAction.submitted_by == "visitor",
        )
        .order_by(WebchatCardAction.id.asc())
        .all()
    )
    for action in candidates:
        stored_payload = parse_public_webchat_json(action.action_payload_json) or {}
        if stored_payload.get("action_id") != payload.action_id:
            continue
        message = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.message_type == "action",
                WebchatMessage.payload_json.like(
                    f'%"action_id": "{payload.action_id}"%'
                ),
            )
            .order_by(WebchatMessage.id.asc())
            .first()
        )
        if not message:
            return None
        return {
            "ok": True,
            "idempotent": True,
            "action_id": action.id,
            "status": action.status,
            "message": public_webchat_message_payload(message),
            "handoff_triggered": (
                payload.action_type == "handoff_request"
                or stored_payload.get("card_type") == "handoff"
                or payload.action_id == "talk_to_human"
            ),
        }
    return None


@router.options("/{full_path:path}")
def webchat_options(full_path: str, request: Request):
    del full_path
    return Response(status_code=204, headers=_public_cors_headers(request))


@router.post("/init")
def init_webchat(
    payload: WebchatInitRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(
        default=None,
        alias="X-Webchat-Visitor-Token",
    ),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    visitor_token = _resolve_visitor_token(
        x_webchat_visitor_token,
        None,
        payload.visitor_token,
    )
    with managed_session(db):
        enforce_webchat_rate_limit(
            db,
            request,
            tenant_key=payload.tenant_key,
            conversation_id=payload.conversation_id,
        )
        result = create_or_resume_conversation(
            db,
            payload.model_copy(update={"visitor_token": visitor_token}),
            request,
        )
    return _apply_webchat_config_defaults(result)


@router.post("/conversations/{conversation_id}/messages")
def send_webchat_message(
    conversation_id: str,
    payload: WebchatSendRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(
        default=None,
        alias="X-Webchat-Visitor-Token",
    ),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    visitor_token = _resolve_visitor_token(
        x_webchat_visitor_token,
        None,
        payload.visitor_token,
    )
    if not visitor_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid webchat visitor token",
        )
    with managed_session(db):
        conversation_query = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        )
        if db.bind and db.bind.dialect.name.startswith("postgresql"):
            conversation_query = conversation_query.with_for_update()
        conversation = conversation_query.first()
        if not conversation:
            raise HTTPException(
                status_code=404,
                detail="webchat conversation not found",
            )
        validate_webchat_visitor_token(
            conversation,
            visitor_token,
        )
        enforce_webchat_rate_limit(
            db,
            request,
            tenant_key=conversation.tenant_key,
            conversation_id=conversation_id,
            authorized_conversation=conversation,
        )
        result = add_visitor_message_to_conversation(
            db,
            conversation=conversation,
            body=payload.body,
            client_message_id=payload.client_message_id,
            message_type="text",
            origin=origin_from_request(request),
        )
        result = _schedule_ai_turn_for_result(
            db,
            conversation=conversation,
            result=result,
        )
    return result


@router.get("/conversations/{conversation_id}/messages")
def poll_webchat_messages(
    conversation_id: str,
    request: Request,
    response: Response,
    visitor_token: str | None = Query(default=None),
    after_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    x_webchat_visitor_token: str | None = Header(
        default=None,
        alias="X-Webchat-Visitor-Token",
    ),
    x_webchat_ws_fallback: str | None = Header(
        default=None,
        alias="X-Webchat-WS-Fallback",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    if str(x_webchat_ws_fallback or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        record_webchat_websocket_fallback_polling(
            "visitor",
            "client_poll",
        )
        log_event(
            20,
            "websocket_fallback_polling",
            client_type="visitor",
            reason="client_poll",
        )
    resolved_token = _resolve_visitor_token(
        x_webchat_visitor_token,
        visitor_token,
    )
    if not resolved_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid webchat visitor token",
        )
    with managed_session(db):
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .first()
        )
        if not conversation:
            raise HTTPException(
                status_code=404,
                detail="webchat conversation not found",
            )
        validate_webchat_visitor_token(
            conversation,
            resolved_token,
        )
        enforce_webchat_rate_limit(
            db,
            request,
            tenant_key=conversation.tenant_key,
            conversation_id=conversation_id,
            authorized_conversation=conversation,
        )
        reconcile_webchat_ai_state(
            db,
            conversation_id=conversation.id,
        )
        result = _attach_ai_snapshot(
            list_public_messages_throttled(
                db,
                conversation,
                after_id=after_id,
                limit=limit,
            ),
            conversation,
        )
    return result


@router.post("/conversations/{conversation_id}/actions")
def submit_webchat_action(
    conversation_id: str,
    payload: WebChatActionSubmitRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(
        default=None,
        alias="X-Webchat-Visitor-Token",
    ),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    visitor_token = _resolve_visitor_token(
        x_webchat_visitor_token,
        None,
        payload.visitor_token,
    )
    if not visitor_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid webchat visitor token",
        )
    with managed_session(db):
        conversation = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == conversation_id)
            .first()
        )
        if not conversation:
            raise HTTPException(
                status_code=404,
                detail="webchat conversation not found",
            )
        validate_webchat_visitor_token(
            conversation,
            visitor_token,
        )
        enforce_webchat_rate_limit(
            db,
            request,
            tenant_key=conversation.tenant_key,
            conversation_id=conversation_id,
            authorized_conversation=conversation,
        )
        existing = _find_existing_action_response(
            db,
            conversation=conversation,
            payload=payload,
        )
        if existing:
            return existing
        result = submit_card_action_to_conversation(
            db,
            conversation=conversation,
            payload=payload,
            request=request,
        )
    return result
