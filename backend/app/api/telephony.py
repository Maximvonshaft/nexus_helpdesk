from __future__ import annotations

import hmac
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..services.livekit_agent_turn_service import process_livekit_agent_turn
from ..services.livekit_telephony_service import (
    list_voice_configurations,
    process_livekit_webhook_payload,
    upsert_voice_configuration,
)
from ..services.permissions import ensure_can_manage_channel_accounts
from ..unit_of_work import managed_session
from ..webchat_voice_config import load_webchat_voice_runtime_config
from .deps import get_current_user

router = APIRouter(prefix="/api/telephony", tags=["telephony"])


class VoiceConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inbound_trunk_id: str | None = Field(default=None, max_length=160)
    outbound_trunk_id: str | None = Field(default=None, max_length=160)
    routing_mode: Literal["ai_first", "human_first"] = "ai_first"
    ai_agent_name: str | None = Field(default=None, max_length=160)
    queue_timeout_seconds: int = Field(default=90, ge=15, le=3600)
    wrap_up_seconds: int = Field(default=30, ge=0, le=900)
    recording_policy: Literal["disabled", "consent_required"] = "disabled"
    enabled: bool = False


class LiveKitAgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=64)
    voice_session_id: str = Field(min_length=1, max_length=64)
    turn_id: int = Field(ge=1, le=1_000_000)
    transcript: str = Field(min_length=1, max_length=2000)
    stt_language: str | None = Field(default=None, max_length=20)
    participant_identity: str | None = Field(default=None, max_length=160)


def _bearer(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


@router.get("/configurations")
def read_voice_configurations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    return {"items": list_voice_configurations(db)}


@router.put("/configurations/{channel_account_id}")
def update_voice_configuration(
    channel_account_id: int,
    payload: VoiceConfigurationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    with managed_session(db):
        return upsert_voice_configuration(
            db,
            actor_id=current_user.id,
            channel_account_id=channel_account_id,
            **payload.model_dump(),
        )


@router.post("/internal/agent-turn")
def livekit_agent_turn(
    payload: LiveKitAgentTurnRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = load_webchat_voice_runtime_config()
    expected = config.livekit_agent_shared_secret or ""
    supplied = _bearer(authorization)
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid LiveKit Agent credential")
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
async def livekit_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = load_webchat_voice_runtime_config()
    if config.provider != "livekit" or not config.livekit_webhook_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LiveKit webhook is disabled")
    if not config.livekit_api_key or not config.livekit_api_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LiveKit webhook is not configured")
    raw_body = await request.body()
    try:
        from google.protobuf.json_format import MessageToDict
        from livekit import api as livekit_api

        receiver = livekit_api.WebhookReceiver(
            livekit_api.TokenVerifier(config.livekit_api_key, config.livekit_api_secret)
        )
        event = receiver.receive(raw_body.decode("utf-8"), authorization or "")
        payload = MessageToDict(event, preserving_proto_field_name=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid LiveKit webhook signature") from exc
    if not isinstance(payload, dict):
        payload = json.loads(raw_body.decode("utf-8"))
    with managed_session(db):
        return process_livekit_webhook_payload(db, payload=payload, raw_body=raw_body)
