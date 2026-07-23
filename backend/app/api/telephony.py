from __future__ import annotations

import hmac
import json
import time
from hashlib import sha256
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..services.livekit_agent_turn_service import process_livekit_agent_turn
from ..services.permissions import (
    ensure_can_control_webcall_voice,
    ensure_can_manage_channel_accounts,
)
from ..services.telephony_configuration_service import (
    list_voice_configurations,
    upsert_voice_configuration,
)
from ..services.telephony_event_service import (
    process_controller_event,
    process_livekit_webhook_event,
)
from ..services.telephony_outbound_service import create_outbound_call
from ..unit_of_work import managed_session
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .deps import get_current_user

router = APIRouter(prefix="/api/telephony", tags=["telephony"])


class VoiceChannelConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    livekit_project_ref: str | None = Field(default=None, max_length=160)
    inbound_trunk_id: str | None = Field(default=None, max_length=160)
    outbound_trunk_id: str | None = Field(default=None, max_length=160)
    dispatch_rule_id: str | None = Field(default=None, max_length=160)
    routing_mode: Literal["ai_first", "human_first"] = "ai_first"
    ai_agent_name: str | None = Field(default=None, max_length=160)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    business_hours: dict[str, list[dict[str, str]]] | None = None
    queue_timeout_seconds: int = Field(default=90, ge=15, le=3600)
    offer_timeout_seconds: int = Field(default=20, ge=5, le=120)
    wrap_up_seconds: int = Field(default=30, ge=0, le=900)
    overflow_action: Literal["ai", "disconnect"] = "ai"
    recording_policy: Literal[
        "disabled",
        "notice",
        "explicit_consent",
    ] = "disabled"
    transcription_policy: Literal[
        "disabled",
        "notice",
        "explicit_consent",
    ] = "disabled"
    enabled: bool = False


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_account_id: int = Field(ge=1)
    phone_number: str = Field(min_length=3, max_length=32)
    mode: Literal["human", "ai"] = "human"
    locale: str | None = Field(default=None, max_length=20)


class LiveKitAgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=64)
    voice_session_id: str = Field(min_length=1, max_length=64)
    turn_id: int = Field(ge=1, le=1_000_000)
    transcript: str = Field(min_length=1, max_length=2000)
    stt_language: str | None = Field(default=None, max_length=20)
    participant_identity: str | None = Field(default=None, max_length=160)


class ControllerEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=180)
    event_type: Literal[
        "controller.joined",
        "controller.heartbeat",
        "controller.left",
        "command.succeeded",
        "command.failed",
        "call.status",
        "compliance.evidence",
    ]
    room_name: str = Field(min_length=1, max_length=160)
    controller_identity: str | None = Field(default=None, max_length=160)
    role: Literal["controller", "ai", "ai_controller"] | None = None
    command_reference: str | None = Field(default=None, max_length=180)
    provider_status: str | None = Field(default=None, max_length=40)
    provider_reason: str | None = Field(default=None, max_length=160)
    safe_result: dict[str, Any] = Field(default_factory=dict)
    call_status: str | None = Field(default=None, max_length=40)


def _bearer(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _require_agent_credential(authorization: str | None) -> None:
    config = load_webchat_voice_runtime_config()
    expected = str(config.livekit_agent_shared_secret or "").strip()
    supplied = _bearer(authorization)
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid LiveKit Agent credential",
        )


def _livekit_webhook_receiver():
    config = load_webchat_voice_runtime_config()
    if config.provider != "livekit" or not config.livekit_webhook_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LiveKit webhook is disabled",
        )
    if not config.livekit_api_key or not config.livekit_api_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit webhook is not configured",
        )
    try:
        from livekit import api as livekit_api
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit webhook verifier is unavailable",
        ) from exc
    return livekit_api.WebhookReceiver(
        livekit_api.TokenVerifier(
            config.livekit_api_key,
            config.livekit_api_secret,
        )
    )


def _verify_controller_signature(
    *,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
) -> None:
    config = load_webchat_voice_runtime_config()
    secret = str(config.livekit_agent_shared_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit controller authentication is not configured",
        )
    try:
        signed_at = int(str(timestamp_header or ""))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid controller event timestamp",
        ) from exc
    if abs(int(time.time()) - signed_at) > 300:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="expired controller event timestamp",
        )
    supplied = str(signature_header or "").strip().lower()
    if supplied.startswith("sha256="):
        supplied = supplied[7:]
    expected = hmac.new(
        secret.encode("utf-8"),
        str(signed_at).encode("ascii") + b"." + body,
        sha256,
    ).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid controller event signature",
        )


def _event_http_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("payload_mismatch"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider event id was reused with different payload",
        )
    if result.get("status") == "retryable":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telephony event projection is temporarily unavailable",
        )
    return result


@router.get("/configurations")
def read_voice_configurations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_manage_channel_accounts(current_user, db)
    return {
        "items": list_voice_configurations(
            db,
            actor=current_user,
        )
    }


@router.put("/configurations/{channel_account_id}")
def update_voice_configuration(
    channel_account_id: int,
    payload: VoiceChannelConfigurationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_manage_channel_accounts(current_user, db)
    with managed_session(db):
        return upsert_voice_configuration(
            db,
            actor=current_user,
            channel_account_id=channel_account_id,
            **payload.model_dump(),
        )


@router.post("/outbound-calls", status_code=status.HTTP_202_ACCEPTED)
def start_outbound_call(
    payload: OutboundCallRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_control_webcall_voice(current_user, db)
    with managed_session(db):
        return create_outbound_call(
            db,
            actor=current_user,
            channel_account_id=payload.channel_account_id,
            phone_number=payload.phone_number,
            mode=payload.mode,
            locale=payload.locale,
        )


@router.post("/internal/agent-turn")
def livekit_agent_turn(
    payload: LiveKitAgentTurnRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_agent_credential(authorization)
    with managed_session(db):
        return process_livekit_agent_turn(
            db,
            conversation_public_id=payload.conversation_id,
            voice_session_public_id=payload.voice_session_id,
            turn_id=payload.turn_id,
            transcript=payload.transcript,
            stt_language=payload.stt_language,
            participant_identity=payload.participant_identity,
        )


@router.post("/livekit/webhook")
async def receive_livekit_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = await request.body()
    try:
        event = _livekit_webhook_receiver().receive(
            body.decode("utf-8"),
            authorization or "",
        )
        from google.protobuf.json_format import MessageToDict

        payload = MessageToDict(
            event,
            preserving_proto_field_name=False,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid LiveKit webhook signature",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid LiveKit webhook payload",
        )
    with managed_session(db):
        result = process_livekit_webhook_event(
            db,
            payload=payload,
            raw_body=body,
        )
    return _event_http_result(result)


@router.post("/livekit/controller-events")
async def receive_controller_event(
    request: Request,
    x_nexus_controller_timestamp: str | None = Header(
        default=None,
        alias="X-Nexus-Controller-Timestamp",
    ),
    x_nexus_controller_signature: str | None = Header(
        default=None,
        alias="X-Nexus-Controller-Signature",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = await request.body()
    _verify_controller_signature(
        body=body,
        timestamp_header=x_nexus_controller_timestamp,
        signature_header=x_nexus_controller_signature,
    )
    try:
        payload = ControllerEventRequest.model_validate_json(body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid LiveKit controller event",
        ) from exc
    with managed_session(db):
        result = process_controller_event(
            db,
            payload=payload.model_dump(),
            raw_body=body,
        )
    return _event_http_result(result)
