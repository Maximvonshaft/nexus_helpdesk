from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

from ..webchat_voice_config import WebchatVoiceRuntimeConfig
from .voice_provider import VoiceParticipantToken, VoiceProvider, VoiceProviderError

logger = logging.getLogger(__name__)


def _server_api_url(livekit_url: str) -> str:
    parsed = urlparse(livekit_url.rstrip("/"))
    if parsed.scheme == "wss":
        return urlunparse(parsed._replace(scheme="https")).rstrip("/")
    if parsed.scheme == "ws":
        return urlunparse(parsed._replace(scheme="http")).rstrip("/")
    return livekit_url.rstrip("/")


def _is_already_exists_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "already" in text and "exist" in text


def _livekit_api_module():
    try:
        from livekit import api as livekit_api
    except Exception as exc:
        raise VoiceProviderError("livekit-api dependency is not installed") from exc
    return livekit_api


def _proto_room_module(livekit_api):
    return getattr(livekit_api, "proto_room", livekit_api)


def _room_request(livekit_api, name: str, **kwargs):
    proto_room = _proto_room_module(livekit_api)
    request_cls = getattr(proto_room, name, None) or getattr(livekit_api, name, None)
    if request_cls is None:
        raise VoiceProviderError(f"livekit-api is missing {name}")
    return request_cls(**kwargs)


@dataclass(frozen=True)
class LiveKitVoiceProvider(VoiceProvider):
    livekit_url: str
    api_key: str
    api_secret: str
    empty_timeout_seconds: int = 900
    max_participants: int = 8

    provider_name = "livekit"

    @classmethod
    def from_config(cls, config: WebchatVoiceRuntimeConfig) -> "LiveKitVoiceProvider":
        if not config.livekit_url or not config.livekit_api_key or not config.livekit_api_secret:
            raise VoiceProviderError("LiveKit provider is missing required configuration")
        return cls(
            livekit_url=config.livekit_url,
            api_key=config.livekit_api_key,
            api_secret=config.livekit_api_secret,
            empty_timeout_seconds=config.session_ttl_seconds,
        )

    @property
    def api_url(self) -> str:
        return _server_api_url(self.livekit_url)

    def create_room(self, *, room_name: str) -> str:
        try:
            self._run(self._create_room_async(room_name=room_name))
        except VoiceProviderError:
            raise
        except Exception as exc:
            if _is_already_exists_error(exc):
                logger.info("livekit_room_already_exists", extra={"room_name": room_name})
                return room_name
            raise VoiceProviderError("livekit room creation failed") from exc
        return room_name

    def issue_participant_token(self, *, room_name: str, participant_identity: str, ttl_seconds: int) -> VoiceParticipantToken:
        livekit_api = _livekit_api_module()
        token = (
            livekit_api.AccessToken(self.api_key, self.api_secret)
            .with_identity(participant_identity)
            .with_ttl(timedelta(seconds=ttl_seconds))
            .with_grants(
                livekit_api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .to_jwt()
        )
        return VoiceParticipantToken(
            provider=self.provider_name,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_token=token,
            expires_in_seconds=ttl_seconds,
        )

    def close_room(self, *, room_name: str) -> None:
        try:
            self._run(self._delete_room_async(room_name=room_name))
        except Exception as exc:
            logger.warning("livekit_room_close_failed", extra={"room_name": room_name, "error_type": type(exc).__name__})
        return None

    def get_room_status(self, *, room_name: str) -> str:
        try:
            found = self._run(self._room_exists_async(room_name=room_name))
        except Exception as exc:
            raise VoiceProviderError("livekit room status lookup failed") from exc
        return "active" if found else "not_found"

    async def _create_room_async(self, *, room_name: str) -> None:
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(url=self.api_url, api_key=self.api_key, api_secret=self.api_secret) as lkapi:
            try:
                await lkapi.room.create_room(
                    _room_request(
                        livekit_api,
                        "CreateRoomRequest",
                        name=room_name,
                        empty_timeout=self.empty_timeout_seconds,
                        max_participants=self.max_participants,
                    )
                )
            except Exception as exc:
                if _is_already_exists_error(exc):
                    return
                raise

    async def _delete_room_async(self, *, room_name: str) -> None:
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(url=self.api_url, api_key=self.api_key, api_secret=self.api_secret) as lkapi:
            await lkapi.room.delete_room(_room_request(livekit_api, "DeleteRoomRequest", room=room_name))

    async def _room_exists_async(self, *, room_name: str) -> bool:
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(url=self.api_url, api_key=self.api_key, api_secret=self.api_secret) as lkapi:
            response = await lkapi.room.list_rooms(_room_request(livekit_api, "ListRoomsRequest", names=[room_name]))
            return bool(getattr(response, "rooms", []))

    @staticmethod
    def _run(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result: dict[str, object] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result.get("value")
