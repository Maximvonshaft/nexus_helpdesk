from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from ...services.livekit_voice_provider import LiveKitVoiceProvider
from ...services.voice_provider import VoiceParticipantToken
from ...services.voice_provider import VoiceProvider
from ...webchat_voice_config import load_webchat_voice_runtime_config
from ...voice_models import WebchatVoiceSession
from .config import WebCallAISettings, get_webcall_ai_settings


@dataclass(frozen=True)
class WebCallAIRoomJoinResult:
    joined: bool
    provider: str
    participant_identity: str
    status: str = "joined"
    error_code: str | None = None


@dataclass(frozen=True)
class WebCallAIRoomLeaveResult:
    left: bool
    provider: str
    participant_identity: str
    status: str = "left"
    error_code: str | None = None


class WebCallAIRoomClient(Protocol):
    def issue_ai_token(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        ttl_seconds: int,
    ) -> VoiceParticipantToken:
        ...

    def join(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        token: VoiceParticipantToken,
    ) -> WebCallAIRoomJoinResult:
        ...

    def leave(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
    ) -> WebCallAIRoomLeaveResult:
        ...


class FakeWebCallAIRoomClient:
    provider = "fake_room_client"

    def issue_ai_token(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        ttl_seconds: int,
    ) -> VoiceParticipantToken:
        digest = hashlib.sha256(
            f"{session.provider_room_name}:{participant_identity}:{ttl_seconds}".encode("utf-8")
        ).hexdigest()[:32]
        return VoiceParticipantToken(
            provider=self.provider,
            room_name=session.provider_room_name,
            participant_identity=participant_identity,
            participant_token=f"fake_ai_participant_token_{digest}",
            expires_in_seconds=ttl_seconds,
        )

    def join(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        token: VoiceParticipantToken,
    ) -> WebCallAIRoomJoinResult:
        return WebCallAIRoomJoinResult(
            joined=True,
            provider=self.provider,
            participant_identity=participant_identity,
        )

    def leave(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
    ) -> WebCallAIRoomLeaveResult:
        return WebCallAIRoomLeaveResult(
            left=True,
            provider=self.provider,
            participant_identity=participant_identity,
        )


class LiveKitTokenIssuerRoomClient:
    provider = "livekit_token_issuer"

    def __init__(self, voice_provider: VoiceProvider) -> None:
        self.voice_provider = voice_provider

    def issue_ai_token(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        ttl_seconds: int,
    ) -> VoiceParticipantToken:
        return self.voice_provider.issue_participant_token(
            room_name=session.provider_room_name,
            participant_identity=participant_identity,
            ttl_seconds=ttl_seconds,
        )

    def join(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        token: VoiceParticipantToken,
    ) -> WebCallAIRoomJoinResult:
        return WebCallAIRoomJoinResult(
            joined=True,
            provider=self.provider,
            participant_identity=participant_identity,
            status="token_issued_no_media_join",
        )

    def leave(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
    ) -> WebCallAIRoomLeaveResult:
        return WebCallAIRoomLeaveResult(
            left=True,
            provider=self.provider,
            participant_identity=participant_identity,
            status="left_no_media_join",
        )


def build_livekit_token_issuer_client(
    voice_provider: VoiceProvider | None = None,
) -> LiveKitTokenIssuerRoomClient:
    if voice_provider is not None:
        return LiveKitTokenIssuerRoomClient(voice_provider)
    runtime_config = load_webchat_voice_runtime_config()
    return LiveKitTokenIssuerRoomClient(LiveKitVoiceProvider.from_config(runtime_config))


def get_webcall_ai_room_client(settings: WebCallAISettings | None = None) -> WebCallAIRoomClient:
    resolved = settings or get_webcall_ai_settings()
    if resolved.participant_mode == "fake_room_client":
        return FakeWebCallAIRoomClient()
    if resolved.participant_mode == "livekit_token_issuer":
        if not resolved.livekit_token_issuer_enabled:
            raise RuntimeError("WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED must be true for livekit_token_issuer")
        return build_livekit_token_issuer_client()
    raise RuntimeError("WEBCALL_AI_PARTICIPANT_MODE must be fake_room_client or livekit_token_issuer in PR-9")
