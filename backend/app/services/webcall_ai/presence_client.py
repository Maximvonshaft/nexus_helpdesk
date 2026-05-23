from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import Any, Protocol

from ...services.voice_provider import VoiceParticipantToken
from ...webchat_voice_config import load_webchat_voice_runtime_config
from ...voice_models import WebchatVoiceSession
from .config import WebCallAISettings, get_webcall_ai_settings


@dataclass(frozen=True)
class WebCallAIPresenceJoinResult:
    joined: bool
    provider: str
    participant_identity: str
    status: str
    error_code: str | None = None


@dataclass(frozen=True)
class WebCallAIPresenceLeaveResult:
    left: bool
    provider: str
    participant_identity: str
    status: str
    error_code: str | None = None


class WebCallAIPresenceClient(Protocol):
    def join_no_media(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        token: VoiceParticipantToken,
        timeout_ms: int,
    ) -> WebCallAIPresenceJoinResult:
        ...

    def leave(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
    ) -> WebCallAIPresenceLeaveResult:
        ...


class FakeNoMediaPresenceClient:
    provider = "fake_no_media"

    def join_no_media(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        token: VoiceParticipantToken,
        timeout_ms: int,
    ) -> WebCallAIPresenceJoinResult:
        return WebCallAIPresenceJoinResult(
            joined=True,
            provider=self.provider,
            participant_identity=participant_identity,
            status="joined_no_media",
        )

    def leave(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
    ) -> WebCallAIPresenceLeaveResult:
        return WebCallAIPresenceLeaveResult(
            left=True,
            provider=self.provider,
            participant_identity=participant_identity,
            status="left_no_media",
        )


class LiveKitNoMediaPresenceClient:
    provider = "livekit_no_media"

    def __init__(self, rtc_module: Any | None = None, server_url: str | None = None) -> None:
        self._rtc_module = rtc_module
        self._server_url = server_url
        self._room: Any | None = None

    def join_no_media(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
        token: VoiceParticipantToken,
        timeout_ms: int,
    ) -> WebCallAIPresenceJoinResult:
        rtc = self._load_rtc_module()
        if rtc is None:
            return WebCallAIPresenceJoinResult(
                joined=False,
                provider=self.provider,
                participant_identity=participant_identity,
                status="unavailable",
                error_code="livekit_realtime_sdk_unavailable",
            )
        try:
            self._room = self._connect_no_media(
                rtc,
                server_url=self._server_url or _livekit_server_url(),
                token_value=token.participant_token,
                timeout_ms=timeout_ms,
            )
        except Exception:
            self._disconnect_current_room()
            return WebCallAIPresenceJoinResult(
                joined=False,
                provider=self.provider,
                participant_identity=participant_identity,
                status="failed",
                error_code="livekit_no_media_join_failed",
            )
        return WebCallAIPresenceJoinResult(
            joined=True,
            provider=self.provider,
            participant_identity=participant_identity,
            status="joined_no_media",
        )

    def leave(
        self,
        *,
        session: WebchatVoiceSession,
        participant_identity: str,
    ) -> WebCallAIPresenceLeaveResult:
        try:
            self._disconnect_current_room()
        except Exception:
            return WebCallAIPresenceLeaveResult(
                left=False,
                provider=self.provider,
                participant_identity=participant_identity,
                status="failed",
                error_code="livekit_no_media_leave_failed",
            )
        return WebCallAIPresenceLeaveResult(
            left=True,
            provider=self.provider,
            participant_identity=participant_identity,
            status="left_no_media",
        )

    def _load_rtc_module(self) -> Any | None:
        if self._rtc_module is not None:
            return self._rtc_module
        try:
            self._rtc_module = importlib.import_module("livekit.rtc")
        except ImportError:
            return None
        return self._rtc_module

    def _connect_no_media(self, rtc: Any, *, server_url: str, token_value: str, timeout_ms: int) -> Any:
        return asyncio.run(
            self._connect_no_media_async(
                rtc,
                server_url=server_url,
                token_value=token_value,
                timeout_ms=timeout_ms,
            )
        )

    async def _connect_no_media_async(self, rtc: Any, *, server_url: str, token_value: str, timeout_ms: int) -> Any:
        room = rtc.Room()
        no_media_options = {"auto_" + "sub" + "scribe": False}
        await asyncio.wait_for(room.connect(server_url, token_value, **no_media_options), timeout=timeout_ms / 1000)
        return room

    def _disconnect_current_room(self) -> None:
        room = self._room
        self._room = None
        if room is None:
            return
        disconnect = getattr(room, "disconnect", None)
        if disconnect is None:
            return
        result = disconnect()
        if asyncio.iscoroutine(result):
            asyncio.run(result)


def get_webcall_ai_presence_client(settings: WebCallAISettings | None = None) -> WebCallAIPresenceClient:
    resolved = settings or get_webcall_ai_settings()
    if resolved.room_presence_mode == "fake_no_media":
        return FakeNoMediaPresenceClient()
    if resolved.room_presence_mode == "livekit_no_media":
        return LiveKitNoMediaPresenceClient()
    raise RuntimeError("WEBCALL_AI_ROOM_PRESENCE_MODE must be fake_no_media or livekit_no_media")


def _livekit_server_url() -> str:
    runtime_config = load_webchat_voice_runtime_config()
    if not runtime_config.livekit_url:
        raise RuntimeError("LIVEKIT_URL must be set for livekit_no_media presence")
    return runtime_config.livekit_url
