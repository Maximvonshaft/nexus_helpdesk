from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

from ..webchat_voice_config import WebchatVoiceRuntimeConfig
from .voice_provider import (
    VoiceParticipantToken,
    VoiceProvider,
    VoiceProviderActionResult,
    VoiceProviderError,
)

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


def _request(livekit_api, request_name: str, **kwargs):
    candidates = (
        livekit_api,
        getattr(livekit_api, "proto_room", None),
        getattr(livekit_api, "proto_sip", None),
        getattr(livekit_api, "proto_agent_dispatch", None),
    )
    for module in candidates:
        request_cls = getattr(module, request_name, None) if module is not None else None
        if request_cls is not None:
            return request_cls(**kwargs)
    raise VoiceProviderError(f"livekit-api is missing {request_name}")


@dataclass(frozen=True)
class LiveKitVoiceProvider(VoiceProvider):
    livekit_url: str
    api_key: str
    api_secret: str
    empty_timeout_seconds: int = 900
    max_participants: int = 16

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

    def issue_participant_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        ttl_seconds: int,
    ) -> VoiceParticipantToken:
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
            logger.warning(
                "livekit_room_close_failed",
                extra={"room_name": room_name, "error_type": type(exc).__name__},
            )

    def get_room_status(self, *, room_name: str) -> str:
        try:
            found = self._run(self._room_exists_async(room_name=room_name))
        except Exception as exc:
            raise VoiceProviderError("livekit room status lookup failed") from exc
        return "active" if found else "not_found"

    def dispatch_agent(
        self,
        *,
        room_name: str,
        agent_name: str,
        metadata: dict[str, Any],
    ) -> VoiceProviderActionResult:
        if not agent_name.strip():
            raise VoiceProviderError("LiveKit Agent name is required")
        try:
            dispatch = self._run(
                self._dispatch_agent_async(
                    room_name=room_name,
                    agent_name=agent_name.strip(),
                    metadata=metadata,
                )
            )
        except Exception as exc:
            raise VoiceProviderError("LiveKit Agent dispatch failed") from exc
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="dispatched",
            provider_reference=str(getattr(dispatch, "id", "") or "") or None,
            safe_payload={"agent_name": agent_name.strip(), "metadata_keys": sorted(metadata)},
        )

    def execute_action(
        self,
        *,
        room_name: str,
        action_type: str,
        target: str | None = None,
        digits: str | None = None,
        participant_identity: str | None = None,
        outbound_trunk_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> VoiceProviderActionResult:
        action = action_type.strip().lower()
        try:
            if action == "transfer":
                if not participant_identity or not target:
                    raise VoiceProviderError("SIP participant identity and transfer target are required")
                result = self._run(
                    self._transfer_sip_async(
                        room_name=room_name,
                        participant_identity=participant_identity,
                        transfer_to=target,
                    )
                )
                return VoiceProviderActionResult(
                    status="succeeded",
                    provider_status="transferred",
                    provider_reference=str(getattr(result, "participant_id", "") or "") or None,
                    safe_payload={"target_present": True},
                )
            if action == "add_participant":
                if not target or not outbound_trunk_id:
                    raise VoiceProviderError("outbound SIP trunk and call target are required")
                identity = f"outbound_{(idempotency_key or 'call')[-48:]}"[:96]
                result = self._run(
                    self._create_sip_participant_async(
                        room_name=room_name,
                        participant_identity=identity,
                        trunk_id=outbound_trunk_id,
                        call_to=target,
                    )
                )
                return VoiceProviderActionResult(
                    status="succeeded",
                    provider_status="participant_created",
                    provider_reference=str(getattr(result, "participant_id", "") or identity),
                    safe_payload={"target_present": True},
                )
            if action not in {"hold", "resume", "mute", "unmute", "keypad"}:
                raise VoiceProviderError("unsupported LiveKit voice action")
            command = {
                "schema": "nexus.telephony.command.v1",
                "action": action,
                "idempotency_key": idempotency_key,
                "digits": digits if action == "keypad" else None,
            }
            self._run(
                self._send_command_async(
                    room_name=room_name,
                    command=command,
                    destination_identity=participant_identity,
                )
            )
            return VoiceProviderActionResult(
                status="succeeded",
                provider_status="command_delivered",
                safe_payload={
                    "destination_present": bool(participant_identity),
                    "digits_length": len(digits or ""),
                },
            )
        except VoiceProviderError:
            raise
        except Exception as exc:
            raise VoiceProviderError(f"LiveKit action failed: {action}") from exc

    async def _create_room_async(self, *, room_name: str) -> None:
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            try:
                await lkapi.room.create_room(
                    _request(
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
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            await lkapi.room.delete_room(
                _request(livekit_api, "DeleteRoomRequest", room=room_name)
            )

    async def _room_exists_async(self, *, room_name: str) -> bool:
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            response = await lkapi.room.list_rooms(
                _request(livekit_api, "ListRoomsRequest", names=[room_name])
            )
            return bool(getattr(response, "rooms", []))

    async def _dispatch_agent_async(
        self,
        *,
        room_name: str,
        agent_name: str,
        metadata: dict[str, Any],
    ):
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            return await lkapi.agent_dispatch.create_dispatch(
                _request(
                    livekit_api,
                    "CreateAgentDispatchRequest",
                    agent_name=agent_name,
                    room=room_name,
                    metadata=json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                )
            )

    async def _transfer_sip_async(
        self,
        *,
        room_name: str,
        participant_identity: str,
        transfer_to: str,
    ):
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            return await lkapi.sip.transfer_sip_participant(
                _request(
                    livekit_api,
                    "TransferSIPParticipantRequest",
                    room_name=room_name,
                    participant_identity=participant_identity,
                    transfer_to=transfer_to,
                    play_dialtone=True,
                )
            )

    async def _create_sip_participant_async(
        self,
        *,
        room_name: str,
        participant_identity: str,
        trunk_id: str,
        call_to: str,
    ):
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            return await lkapi.sip.create_sip_participant(
                _request(
                    livekit_api,
                    "CreateSIPParticipantRequest",
                    room_name=room_name,
                    participant_identity=participant_identity,
                    participant_name="Nexus outbound participant",
                    sip_trunk_id=trunk_id,
                    sip_call_to=call_to,
                    wait_until_answered=False,
                )
            )

    async def _send_command_async(
        self,
        *,
        room_name: str,
        command: dict[str, Any],
        destination_identity: str | None,
    ) -> None:
        livekit_api = _livekit_api_module()
        kwargs: dict[str, Any] = {
            "room": room_name,
            "data": json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "topic": "nexus.telephony.command.v1",
        }
        if destination_identity:
            kwargs["destination_identities"] = [destination_identity]
        data_packet = getattr(livekit_api, "DataPacket", None)
        if data_packet is not None and getattr(data_packet, "RELIABLE", None) is not None:
            kwargs["kind"] = data_packet.RELIABLE
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            await lkapi.room.send_data(_request(livekit_api, "SendDataRequest", **kwargs))

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
