from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

from ..livekit_voice_provider import LiveKitVoiceProvider
from ..mock_voice_provider import MockVoiceProvider
from ..voice_provider import VoiceParticipantToken, VoiceProvider, VoiceProviderError
from ...webchat_voice_config import load_webchat_voice_runtime_config
from .config import WebCallAIProductionSettings, get_webcall_ai_production_settings


@dataclass(frozen=True)
class WebCallAIToken:
    provider: str
    room_name: str
    participant_identity: str
    participant_token: str
    expires_in_seconds: int


def _provider(settings: WebCallAIProductionSettings | None = None) -> VoiceProvider:
    runtime = settings or get_webcall_ai_production_settings()
    if runtime.webchat_voice_provider == "mock":
        return MockVoiceProvider()
    if runtime.webchat_voice_provider == "livekit":
        try:
            return LiveKitVoiceProvider.from_config(load_webchat_voice_runtime_config())
        except VoiceProviderError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="voice provider is not available")


def create_room(room_name: str, settings: WebCallAIProductionSettings | None = None) -> str:
    return _provider(settings).create_room(room_name=room_name)


def close_room(room_name: str, settings: WebCallAIProductionSettings | None = None) -> None:
    _provider(settings).close_room(room_name=room_name)


def issue_join_token(
    *,
    room_name: str,
    participant_identity: str,
    ttl_seconds: int | None = None,
    settings: WebCallAIProductionSettings | None = None,
) -> WebCallAIToken:
    runtime = settings or get_webcall_ai_production_settings()
    issued: VoiceParticipantToken = _provider(runtime).issue_participant_token(
        room_name=room_name,
        participant_identity=participant_identity,
        ttl_seconds=ttl_seconds or runtime.max_session_seconds,
    )
    return WebCallAIToken(
        provider=issued.provider,
        room_name=issued.room_name,
        participant_identity=issued.participant_identity,
        participant_token=issued.participant_token,
        expires_in_seconds=issued.expires_in_seconds,
    )

