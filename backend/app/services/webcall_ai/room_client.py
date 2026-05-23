from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from ...services.voice_provider import VoiceParticipantToken
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


def get_webcall_ai_room_client(settings: WebCallAISettings | None = None) -> WebCallAIRoomClient:
    resolved = settings or get_webcall_ai_settings()
    if resolved.participant_mode == "fake_room_client":
        return FakeWebCallAIRoomClient()
    raise RuntimeError("WEBCALL_AI_PARTICIPANT_MODE must be fake_room_client in PR-8")
