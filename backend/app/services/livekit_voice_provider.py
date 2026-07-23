from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

from ..settings import get_settings
from ..webchat_voice_config import WebchatVoiceRuntimeConfig
from .voice_provider import (
    VoiceParticipantToken,
    VoiceProvider,
    VoiceProviderActionResult,
    VoiceProviderError,
)

logger = logging.getLogger(__name__)

_CONTROLLER_ACTIONS = {"hold", "resume", "keypad", "warm_transfer"}
_DIRECT_ACTIONS = {
    "hangup",
    "mute",
    "unmute",
    "add_participant",
    "remove_participant",
    "cold_transfer",
    "recording_start",
    "recording_stop",
}


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
        getattr(livekit_api, "proto_egress", None),
        getattr(livekit_api, "proto_models", None),
    )
    for module in candidates:
        request_cls = getattr(module, request_name, None) if module is not None else None
        if request_cls is not None:
            return request_cls(**kwargs)
    raise VoiceProviderError(f"livekit-api is missing {request_name}")


def _enum_value(livekit_api, enum_name: str, member_name: str):
    candidates = (
        livekit_api,
        getattr(livekit_api, "proto_room", None),
        getattr(livekit_api, "proto_egress", None),
        getattr(livekit_api, "proto_models", None),
    )
    for module in candidates:
        if module is None:
            continue
        enum_type = getattr(module, enum_name, None)
        if enum_type is not None and getattr(enum_type, member_name, None) is not None:
            return getattr(enum_type, member_name)
        if getattr(module, member_name, None) is not None:
            return getattr(module, member_name)
    return None


def _provider_reference(value: Any, *attribute_names: str) -> str | None:
    for attribute_name in attribute_names:
        candidate = str(getattr(value, attribute_name, "") or "").strip()
        if candidate:
            return candidate
    return None


@dataclass(frozen=True)
class LiveKitVoiceProvider(VoiceProvider):
    livekit_url: str
    api_key: str
    api_secret: str
    empty_timeout_seconds: int = 900
    max_participants: int = 16
    recording_bucket: str | None = None
    recording_region: str | None = None
    recording_endpoint: str | None = None
    recording_access_key: str | None = None
    recording_secret_key: str | None = None

    provider_name = "livekit"

    @classmethod
    def from_config(cls, config: WebchatVoiceRuntimeConfig) -> "LiveKitVoiceProvider":
        if not config.livekit_url or not config.livekit_api_key or not config.livekit_api_secret:
            raise VoiceProviderError("LiveKit provider is missing required configuration")
        settings = get_settings()
        return cls(
            livekit_url=config.livekit_url,
            api_key=config.livekit_api_key,
            api_secret=config.livekit_api_secret,
            empty_timeout_seconds=config.session_ttl_seconds,
            recording_bucket=(str(settings.s3_bucket or "").strip() or None),
            recording_region=(str(settings.s3_region or "").strip() or None),
            recording_endpoint=(str(settings.s3_endpoint_url or "").strip() or None),
            recording_access_key=(str(settings.s3_access_key or "").strip() or None),
            recording_secret_key=(str(settings.s3_secret_key or "").strip() or None),
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
            raise VoiceProviderError("livekit room close failed") from exc

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
        normalized_name = agent_name.strip()
        if not normalized_name:
            raise VoiceProviderError("LiveKit Agent name is required")
        try:
            dispatch = self._run(
                self._dispatch_agent_async(
                    room_name=room_name,
                    agent_name=normalized_name,
                    metadata=metadata,
                )
            )
        except Exception as exc:
            raise VoiceProviderError("LiveKit Agent dispatch failed") from exc
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="dispatched",
            provider_reference=_provider_reference(dispatch, "id", "dispatch_id"),
            safe_payload={
                "agent_name": normalized_name,
                "metadata_keys": sorted(metadata),
            },
        )

    def execute_action(
        self,
        *,
        room_name: str,
        action_type: str,
        target: str | None = None,
        digits: str | None = None,
        participant_identity: str | None = None,
        human_identity: str | None = None,
        controller_identity: str | None = None,
        outbound_trunk_id: str | None = None,
        recording_reference: str | None = None,
        idempotency_key: str | None = None,
    ) -> VoiceProviderActionResult:
        action = action_type.strip().lower()
        if action not in _DIRECT_ACTIONS | _CONTROLLER_ACTIONS:
            raise VoiceProviderError("unsupported LiveKit voice action")
        try:
            if action == "cold_transfer":
                return self._cold_transfer(
                    room_name=room_name,
                    participant_identity=participant_identity,
                    target=target,
                )
            if action == "add_participant":
                return self._add_participant(
                    room_name=room_name,
                    target=target,
                    outbound_trunk_id=outbound_trunk_id,
                    idempotency_key=idempotency_key,
                )
            if action in {"hangup", "remove_participant"}:
                identity = target if action == "remove_participant" else participant_identity
                return self._remove_participant(
                    room_name=room_name,
                    participant_identity=identity,
                    action=action,
                )
            if action in {"mute", "unmute"}:
                identity = target or participant_identity
                return self._set_participant_muted(
                    room_name=room_name,
                    participant_identity=identity,
                    muted=action == "mute",
                )
            if action == "recording_start":
                return self._start_recording(room_name=room_name)
            if action == "recording_stop":
                return self._stop_recording(recording_reference=recording_reference or target)
            return self._send_controller_command(
                room_name=room_name,
                controller_identity=controller_identity,
                action=action,
                target=target,
                digits=digits,
                participant_identity=participant_identity,
                human_identity=human_identity,
                outbound_trunk_id=outbound_trunk_id,
                idempotency_key=idempotency_key,
            )
        except VoiceProviderError:
            raise
        except Exception as exc:
            raise VoiceProviderError(f"LiveKit action failed: {action}") from exc

    def _cold_transfer(
        self,
        *,
        room_name: str,
        participant_identity: str | None,
        target: str | None,
    ) -> VoiceProviderActionResult:
        if not participant_identity or not target:
            raise VoiceProviderError(
                "SIP participant identity and transfer target are required"
            )
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
            provider_reference=_provider_reference(
                result,
                "participant_id",
                "participant_identity",
                "sip_call_id",
            ),
            safe_payload={"target_present": True},
        )

    def _add_participant(
        self,
        *,
        room_name: str,
        target: str | None,
        outbound_trunk_id: str | None,
        idempotency_key: str | None,
    ) -> VoiceProviderActionResult:
        if not target or not outbound_trunk_id:
            raise VoiceProviderError("outbound SIP trunk and call target are required")
        identity_seed = hashlib.sha256(
            (idempotency_key or target).encode("utf-8")
        ).hexdigest()[:40]
        identity = f"outbound_{identity_seed}"[:96]
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
            provider_reference=_provider_reference(
                result,
                "participant_id",
                "participant_identity",
                "sip_call_id",
            )
            or identity,
            safe_payload={"target_present": True},
        )

    def _remove_participant(
        self,
        *,
        room_name: str,
        participant_identity: str | None,
        action: str,
    ) -> VoiceProviderActionResult:
        if not participant_identity:
            raise VoiceProviderError("participant identity is required")
        self._run(
            self._remove_participant_async(
                room_name=room_name,
                participant_identity=participant_identity,
            )
        )
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="participant_removed",
            provider_reference=participant_identity,
            safe_payload={"action": action},
        )

    def _set_participant_muted(
        self,
        *,
        room_name: str,
        participant_identity: str | None,
        muted: bool,
    ) -> VoiceProviderActionResult:
        if not participant_identity:
            raise VoiceProviderError("participant identity is required")
        track_sids = self._run(
            self._mute_participant_tracks_async(
                room_name=room_name,
                participant_identity=participant_identity,
                muted=muted,
            )
        )
        if not track_sids:
            raise VoiceProviderError("participant has no published media track")
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="muted" if muted else "unmuted",
            provider_reference=participant_identity,
            safe_payload={"track_count": len(track_sids)},
        )

    def _start_recording(self, *, room_name: str) -> VoiceProviderActionResult:
        if not self.recording_bucket:
            raise VoiceProviderError("voice recording object storage is not configured")
        if bool(self.recording_access_key) != bool(self.recording_secret_key):
            raise VoiceProviderError("voice recording object storage credentials are incomplete")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filepath = f"voice-recordings/{room_name}/{timestamp}.ogg"
        result = self._run(
            self._start_recording_async(
                room_name=room_name,
                filepath=filepath,
            )
        )
        reference = _provider_reference(result, "egress_id")
        if not reference:
            raise VoiceProviderError("LiveKit recording did not return an egress id")
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="recording_started",
            provider_reference=reference,
            safe_payload={"object_key": filepath},
        )

    def _stop_recording(
        self,
        *,
        recording_reference: str | None,
    ) -> VoiceProviderActionResult:
        if not recording_reference:
            raise VoiceProviderError("recording egress id is required")
        result = self._run(
            self._stop_recording_async(egress_id=recording_reference)
        )
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="recording_stopped",
            provider_reference=_provider_reference(result, "egress_id")
            or recording_reference,
            safe_payload={},
        )

    def _send_controller_command(
        self,
        *,
        room_name: str,
        controller_identity: str | None,
        action: str,
        target: str | None,
        digits: str | None,
        participant_identity: str | None,
        human_identity: str | None,
        outbound_trunk_id: str | None,
        idempotency_key: str | None,
    ) -> VoiceProviderActionResult:
        if not controller_identity:
            raise VoiceProviderError("active telephony controller identity is required")
        if action in {"hold", "resume"}:
            if not participant_identity:
                raise VoiceProviderError("participant identity is required")
            if not human_identity:
                raise VoiceProviderError("active human participant identity is required")
        if action == "keypad" and not digits:
            raise VoiceProviderError("DTMF digits are required")
        if action == "warm_transfer" and (not target or not outbound_trunk_id):
            raise VoiceProviderError("warm transfer target and outbound trunk are required")
        reference = idempotency_key or hashlib.sha256(
            json.dumps(
                {
                    "room": room_name,
                    "action": action,
                    "controller": controller_identity,
                    "participant": participant_identity,
                    "human": human_identity,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        command = {
            "schema": "nexus.telephony.command.v1",
            "command_id": reference,
            "action": action,
            "target": target,
            "digits": digits if action == "keypad" else None,
            "participant_identity": participant_identity,
            "human_identity": human_identity if action in {"hold", "resume"} else None,
            "outbound_trunk_id": outbound_trunk_id if action == "warm_transfer" else None,
        }
        self._run(
            self._send_command_async(
                room_name=room_name,
                command=command,
                destination_identity=controller_identity,
            )
        )
        return VoiceProviderActionResult(
            status="awaiting_event",
            provider_status="command_delivered",
            provider_reference=reference,
            safe_payload={
                "controller_identity_present": True,
                "participant_identity_present": bool(participant_identity),
                "human_identity_present": bool(human_identity),
                "target_present": bool(target),
                "digits_length": len(digits or ""),
            },
        )

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
                    metadata=json.dumps(
                        metadata,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
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

    async def _remove_participant_async(
        self,
        *,
        room_name: str,
        participant_identity: str,
    ) -> None:
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            await lkapi.room.remove_participant(
                _request(
                    livekit_api,
                    "RoomParticipantIdentity",
                    room=room_name,
                    identity=participant_identity,
                )
            )

    async def _mute_participant_tracks_async(
        self,
        *,
        room_name: str,
        participant_identity: str,
        muted: bool,
    ) -> list[str]:
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            participant = await lkapi.room.get_participant(
                _request(
                    livekit_api,
                    "RoomParticipantIdentity",
                    room=room_name,
                    identity=participant_identity,
                )
            )
            track_sids = [
                str(getattr(track, "sid", "") or "").strip()
                for track in getattr(participant, "tracks", [])
                if str(getattr(track, "sid", "") or "").strip()
            ]
            for track_sid in track_sids:
                await lkapi.room.mute_published_track(
                    _request(
                        livekit_api,
                        "MuteRoomTrackRequest",
                        room=room_name,
                        identity=participant_identity,
                        track_sid=track_sid,
                        muted=muted,
                    )
                )
            return track_sids

    async def _start_recording_async(
        self,
        *,
        room_name: str,
        filepath: str,
    ):
        livekit_api = _livekit_api_module()
        s3_kwargs: dict[str, Any] = {
            "bucket": self.recording_bucket,
            "region": self.recording_region or "us-east-1",
            "endpoint": self.recording_endpoint or "",
            "force_path_style": bool(self.recording_endpoint),
        }
        if self.recording_access_key and self.recording_secret_key:
            s3_kwargs["access_key"] = self.recording_access_key
            s3_kwargs["secret"] = self.recording_secret_key
        s3_upload = _request(livekit_api, "S3Upload", **s3_kwargs)
        file_type = _enum_value(livekit_api, "EncodedFileType", "OGG")
        output_kwargs: dict[str, Any] = {
            "filepath": filepath,
            "s3": s3_upload,
        }
        if file_type is not None:
            output_kwargs["file_type"] = file_type
        output = _request(livekit_api, "EncodedFileOutput", **output_kwargs)
        request = _request(
            livekit_api,
            "RoomCompositeEgressRequest",
            room_name=room_name,
            audio_only=True,
            file_outputs=[output],
        )
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            return await lkapi.egress.start_room_composite_egress(request)

    async def _stop_recording_async(self, *, egress_id: str):
        livekit_api = _livekit_api_module()
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            return await lkapi.egress.stop_egress(
                _request(livekit_api, "StopEgressRequest", egress_id=egress_id)
            )

    async def _send_command_async(
        self,
        *,
        room_name: str,
        command: dict[str, Any],
        destination_identity: str,
    ) -> None:
        livekit_api = _livekit_api_module()
        kwargs: dict[str, Any] = {
            "room": room_name,
            "data": json.dumps(
                command,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            "topic": "nexus.telephony.command.v1",
            "destination_identities": [destination_identity],
        }
        data_packet = getattr(livekit_api, "DataPacket", None)
        if data_packet is not None and getattr(data_packet, "RELIABLE", None) is not None:
            kwargs["kind"] = data_packet.RELIABLE
        async with livekit_api.LiveKitAPI(
            url=self.api_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as lkapi:
            await lkapi.room.send_data(
                _request(livekit_api, "SendDataRequest", **kwargs)
            )

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
