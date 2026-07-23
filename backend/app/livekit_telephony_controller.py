from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from livekit import api as livekit_api
from livekit import rtc
from livekit.agents import BackgroundAudioPlayer, BuiltinAudioClip, JobContext

logger = logging.getLogger("nexus.livekit-agent")
_COMMAND_TOPIC = "nexus.telephony.command.v1"
_ALLOWED_COMMANDS = {
    "hold",
    "resume",
    "keypad",
    "warm_transfer",
    "warm_transfer_complete",
    "warm_transfer_cancel",
}
_DTMF_CODE = {
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "*": 10,
    "#": 11,
    "A": 12,
    "B": 13,
    "C": 14,
    "D": 15,
}
_CONSULTATION_IDENTITY_PREFIX = "consult_"


@dataclass(frozen=True)
class WarmConsultationState:
    command_id: str
    target_identity: str
    caller_identity: str
    human_identity: str

    @property
    def safe_id(self) -> str:
        return hashlib.sha256(self.target_identity.encode("utf-8")).hexdigest()[:20]


def parse_controller_command(packet: Any) -> dict[str, Any] | None:
    if str(getattr(packet, "topic", "") or "") != _COMMAND_TOPIC:
        return None
    raw = getattr(packet, "data", b"")
    try:
        value = __import__("json").loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != _COMMAND_TOPIC:
        return None
    command_id = str(value.get("command_id") or "").strip()
    action = str(value.get("action") or "").strip().lower()
    if not command_id or action not in _ALLOWED_COMMANDS:
        return None
    return {
        "command_id": command_id[:180],
        "action": action,
        "target": str(value.get("target") or "").strip()[:240] or None,
        "digits": str(value.get("digits") or "").strip()[:64] or None,
        "participant_identity": str(
            value.get("participant_identity") or ""
        ).strip()[:160]
        or None,
        "human_identity": str(value.get("human_identity") or "").strip()[:160]
        or None,
        "outbound_trunk_id": str(
            value.get("outbound_trunk_id") or ""
        ).strip()[:160]
        or None,
    }


async def publish_dtmf_sequence(local_participant: Any, digits: str) -> int:
    sent = 0
    for raw_digit in str(digits or ""):
        if raw_digit in {"w", "W"}:
            await asyncio.sleep(0.5)
            continue
        digit = raw_digit.upper()
        code = _DTMF_CODE.get(digit)
        if code is None:
            raise ValueError("invalid_dtmf_digit")
        await local_participant.publish_dtmf(code=code, digit=digit)
        sent += 1
    if sent == 0:
        raise ValueError("dtmf_digits_required")
    return sent


def _server_api_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip().rstrip("/"))
    if parsed.scheme == "wss":
        return urlunparse(parsed._replace(scheme="https"))
    if parsed.scheme == "ws":
        return urlunparse(parsed._replace(scheme="http"))
    return str(value or "").strip().rstrip("/")


def _request(request_name: str, **kwargs: Any):
    candidates = (
        livekit_api,
        getattr(livekit_api, "proto_room", None),
        getattr(livekit_api, "proto_sip", None),
        getattr(livekit_api, "proto_models", None),
    )
    for module in candidates:
        request_cls = getattr(module, request_name, None) if module is not None else None
        if request_cls is not None:
            return request_cls(**kwargs)
    raise RuntimeError(f"livekit_api_missing_{request_name}")


async def _participant_track_sids(
    lkapi: Any,
    *,
    room_name: str,
    participant_identity: str,
) -> list[str]:
    participant = await lkapi.room.get_participant(
        _request(
            "RoomParticipantIdentity",
            room=room_name,
            identity=participant_identity,
        )
    )
    return [
        str(getattr(track, "sid", "") or "").strip()
        for track in getattr(participant, "tracks", [])
        if str(getattr(track, "sid", "") or "").strip()
    ]


async def _wait_for_participant_tracks(
    lkapi: Any,
    *,
    room_name: str,
    participant_identity: str,
    timeout_seconds: float = 8.0,
) -> list[str]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            tracks = await _participant_track_sids(
                lkapi,
                room_name=room_name,
                participant_identity=participant_identity,
            )
            if tracks:
                return tracks
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(0.2)
    if last_error is not None:
        raise RuntimeError("participant_media_not_ready") from last_error
    raise RuntimeError("participant_media_not_ready")


async def _update_subscriptions(
    lkapi: Any,
    *,
    room_name: str,
    subscriber_identity: str,
    track_sids: list[str],
    subscribe: bool,
) -> None:
    if not track_sids:
        raise RuntimeError("participant_has_no_published_media_track")
    await lkapi.room.update_subscriptions(
        _request(
            "UpdateSubscriptionsRequest",
            room=room_name,
            identity=subscriber_identity,
            track_sids=track_sids,
            subscribe=subscribe,
        )
    )


async def set_bidirectional_hold_subscriptions(
    lkapi: Any,
    *,
    room_name: str,
    caller_identity: str,
    human_identity: str,
    subscribe: bool,
) -> dict[str, int]:
    caller_tracks = await _participant_track_sids(
        lkapi,
        room_name=room_name,
        participant_identity=caller_identity,
    )
    human_tracks = await _participant_track_sids(
        lkapi,
        room_name=room_name,
        participant_identity=human_identity,
    )
    operations = (
        (human_identity, caller_tracks),
        (caller_identity, human_tracks),
    )
    completed: list[tuple[str, list[str]]] = []
    try:
        for subscriber_identity, track_sids in operations:
            await _update_subscriptions(
                lkapi,
                room_name=room_name,
                subscriber_identity=subscriber_identity,
                track_sids=track_sids,
                subscribe=subscribe,
            )
            completed.append((subscriber_identity, track_sids))
    except Exception:
        for subscriber_identity, track_sids in reversed(completed):
            try:
                await _update_subscriptions(
                    lkapi,
                    room_name=room_name,
                    subscriber_identity=subscriber_identity,
                    track_sids=track_sids,
                    subscribe=not subscribe,
                )
            except Exception:
                logger.exception(
                    "livekit_hold_subscription_compensation_failed",
                    extra={
                        "room_name": room_name,
                        "subscriber_identity_hash": hashlib.sha256(
                            subscriber_identity.encode("utf-8")
                        ).hexdigest(),
                    },
                )
        raise
    return {
        "caller_track_count": len(caller_tracks),
        "human_track_count": len(human_tracks),
    }


async def _remove_participant(
    lkapi: Any,
    *,
    room_name: str,
    participant_identity: str,
) -> None:
    await lkapi.room.remove_participant(
        _request(
            "RoomParticipantIdentity",
            room=room_name,
            identity=participant_identity,
        )
    )


def _consultation_identity(command_id: str) -> str:
    digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:40]
    return f"{_CONSULTATION_IDENTITY_PREFIX}{digest}"


def _bounded_reason(exc: Exception) -> str:
    value = str(exc or type(exc).__name__).strip().lower().replace(" ", "_")
    safe = "".join(
        character
        for character in value
        if character.isalnum() or character in "_-:"
    )
    return (safe or type(exc).__name__.lower())[:160]


class TelephonyController:
    """The sole deterministic LiveKit Room control adapter."""

    def __init__(
        self,
        *,
        ctx: JobContext,
        client: Any,
        metadata: Any,
        background_audio: BackgroundAudioPlayer,
    ) -> None:
        self._ctx = ctx
        self._client = client
        self._metadata = metadata
        self._background_audio = background_audio
        self._hold_handle: Any = None
        self._hold_lock = asyncio.Lock()
        self._consultation_lock = asyncio.Lock()
        self._consultation: WarmConsultationState | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def register(self) -> None:
        @self._ctx.room.on("data_received")
        def _on_data(packet: rtc.DataPacket) -> None:
            command = parse_controller_command(packet)
            if command is not None:
                self._spawn(self._execute(command))

        @self._ctx.room.on("participant_disconnected")
        def _on_participant_disconnected(participant: Any) -> None:
            identity = str(getattr(participant, "identity", "") or "").strip()
            if identity:
                self._spawn(self._recover_disconnected_consultation(identity))

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._stop_hold_audio()
        self._consultation = None

    def _start_hold_audio(self) -> None:
        if self._hold_handle is None or self._hold_handle.done():
            self._hold_handle = self._background_audio.play(
                BuiltinAudioClip.HOLD_MUSIC.path(),
                loop=True,
            )

    def _stop_hold_audio(self) -> None:
        if self._hold_handle is not None and not self._hold_handle.done():
            self._hold_handle.stop()
        self._hold_handle = None

    def _livekit_api(self):
        return livekit_api.LiveKitAPI(
            url=_server_api_url(os.environ["LIVEKIT_URL"]),
            api_key=os.environ["LIVEKIT_API_KEY"],
            api_secret=os.environ["LIVEKIT_API_SECRET"],
        )

    async def _execute(self, command: dict[str, Any]) -> None:
        command_id = command["command_id"]
        try:
            result = await self._execute_action(command)
        except Exception as exc:
            logger.exception(
                "livekit_controller_command_failed",
                extra={
                    "voice_session_id": self._metadata.voice_session_id,
                    "command_id": command_id,
                    "action": command["action"],
                    "error_type": type(exc).__name__,
                },
            )
            await self._client.controller_event(
                event_type="command.failed",
                room_name=self._ctx.room.name,
                controller_identity=self._ctx.room.local_participant.identity,
                role=self._metadata.role,
                command_reference=command_id,
                provider_status="failed",
                provider_reason=_bounded_reason(exc),
                safe_result={"action": command["action"]},
            )
            return
        await self._client.controller_event(
            event_type="command.succeeded",
            room_name=self._ctx.room.name,
            controller_identity=self._ctx.room.local_participant.identity,
            role=self._metadata.role,
            command_reference=command_id,
            provider_status="succeeded",
            safe_result=result,
        )

    async def _execute_action(self, command: dict[str, Any]) -> dict[str, Any]:
        action = command["action"]
        if action == "keypad":
            sent = await publish_dtmf_sequence(
                self._ctx.room.local_participant,
                command.get("digits") or "",
            )
            return {"action": action, "digits_sent": sent}
        if action in {"hold", "resume"}:
            return await self._set_hold_state(command, held=action == "hold")
        if action == "warm_transfer":
            return await self._start_warm_consultation(command)
        if action == "warm_transfer_complete":
            return await self._complete_warm_consultation(command)
        if action == "warm_transfer_cancel":
            return await self._cancel_warm_consultation(command)
        raise RuntimeError("unsupported_controller_action")

    async def _set_hold_state(
        self,
        command: dict[str, Any],
        *,
        held: bool,
    ) -> dict[str, Any]:
        caller_identity = str(command.get("participant_identity") or "").strip()
        human_identity = str(command.get("human_identity") or "").strip()
        if not caller_identity or not human_identity:
            raise RuntimeError("hold_participant_identity_missing")
        async with self._hold_lock:
            if self._consultation is not None:
                raise RuntimeError("hold_state_owned_by_warm_consultation")
            if held:
                self._start_hold_audio()
            try:
                async with self._livekit_api() as lkapi:
                    counts = await set_bidirectional_hold_subscriptions(
                        lkapi,
                        room_name=self._ctx.room.name,
                        caller_identity=caller_identity,
                        human_identity=human_identity,
                        subscribe=not held,
                    )
            except Exception:
                if held:
                    self._stop_hold_audio()
                raise
            if not held:
                self._stop_hold_audio()
            return {
                "action": "hold" if held else "resume",
                "held": held,
                **counts,
            }

    async def _start_warm_consultation(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        caller_identity = str(command.get("participant_identity") or "").strip()
        human_identity = str(command.get("human_identity") or "").strip()
        target = str(command.get("target") or "").strip()
        trunk_id = str(command.get("outbound_trunk_id") or "").strip()
        if not caller_identity or not human_identity:
            raise RuntimeError("warm_transfer_participant_identity_missing")
        if not target or not trunk_id:
            raise RuntimeError("warm_transfer_target_or_trunk_missing")
        command_id = str(command["command_id"])
        target_identity = _consultation_identity(command_id)
        async with self._consultation_lock:
            if self._consultation is not None:
                if self._consultation.command_id == command_id:
                    return {
                        "action": "warm_transfer",
                        "phase": "consulting",
                        "consultation_id": self._consultation.safe_id,
                        "idempotent": True,
                    }
                raise RuntimeError("warm_transfer_consultation_already_active")
            self._start_hold_audio()
            caller_human_isolated = False
            target_created = False
            try:
                async with self._livekit_api() as lkapi:
                    await set_bidirectional_hold_subscriptions(
                        lkapi,
                        room_name=self._ctx.room.name,
                        caller_identity=caller_identity,
                        human_identity=human_identity,
                        subscribe=False,
                    )
                    caller_human_isolated = True
                    await lkapi.sip.create_sip_participant(
                        _request(
                            "CreateSIPParticipantRequest",
                            room_name=self._ctx.room.name,
                            participant_identity=target_identity,
                            participant_name="Nexus transfer consultation",
                            sip_trunk_id=trunk_id,
                            sip_call_to=target,
                            wait_until_answered=True,
                        )
                    )
                    target_created = True
                    await _wait_for_participant_tracks(
                        lkapi,
                        room_name=self._ctx.room.name,
                        participant_identity=target_identity,
                    )
                    await set_bidirectional_hold_subscriptions(
                        lkapi,
                        room_name=self._ctx.room.name,
                        caller_identity=caller_identity,
                        human_identity=target_identity,
                        subscribe=False,
                    )
            except Exception:
                try:
                    async with self._livekit_api() as lkapi:
                        if target_created:
                            try:
                                await _remove_participant(
                                    lkapi,
                                    room_name=self._ctx.room.name,
                                    participant_identity=target_identity,
                                )
                            except Exception:
                                logger.warning(
                                    "livekit_warm_consult_target_cleanup_failed",
                                    extra={"voice_session_id": self._metadata.voice_session_id},
                                )
                        if caller_human_isolated:
                            await set_bidirectional_hold_subscriptions(
                                lkapi,
                                room_name=self._ctx.room.name,
                                caller_identity=caller_identity,
                                human_identity=human_identity,
                                subscribe=True,
                            )
                finally:
                    self._stop_hold_audio()
                raise
            self._consultation = WarmConsultationState(
                command_id=command_id,
                target_identity=target_identity,
                caller_identity=caller_identity,
                human_identity=human_identity,
            )
            return {
                "action": "warm_transfer",
                "phase": "consulting",
                "consultation_id": self._consultation.safe_id,
                "customer_isolated": True,
            }

    def _find_consultation_target(self) -> str | None:
        if self._consultation is not None:
            return self._consultation.target_identity
        for identity in self._ctx.room.remote_participants:
            if str(identity).startswith(_CONSULTATION_IDENTITY_PREFIX):
                return str(identity)
        return None

    async def _complete_warm_consultation(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        caller_identity = str(command.get("participant_identity") or "").strip()
        human_identity = str(command.get("human_identity") or "").strip()
        if not caller_identity or not human_identity:
            raise RuntimeError("warm_transfer_participant_identity_missing")
        async with self._consultation_lock:
            target_identity = self._find_consultation_target()
            if not target_identity:
                raise RuntimeError("warm_transfer_consultation_not_active")
            async with self._livekit_api() as lkapi:
                await set_bidirectional_hold_subscriptions(
                    lkapi,
                    room_name=self._ctx.room.name,
                    caller_identity=caller_identity,
                    human_identity=target_identity,
                    subscribe=True,
                )
                try:
                    await _remove_participant(
                        lkapi,
                        room_name=self._ctx.room.name,
                        participant_identity=human_identity,
                    )
                except Exception:
                    await set_bidirectional_hold_subscriptions(
                        lkapi,
                        room_name=self._ctx.room.name,
                        caller_identity=caller_identity,
                        human_identity=target_identity,
                        subscribe=False,
                    )
                    raise
            safe_id = hashlib.sha256(target_identity.encode("utf-8")).hexdigest()[:20]
            self._consultation = None
            self._stop_hold_audio()
            return {
                "action": "warm_transfer_complete",
                "phase": "completed",
                "consultation_id": safe_id,
                "customer_bridged": True,
                "previous_operator_removed": True,
            }

    async def _cancel_warm_consultation(
        self,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        caller_identity = str(command.get("participant_identity") or "").strip()
        human_identity = str(command.get("human_identity") or "").strip()
        if not caller_identity or not human_identity:
            raise RuntimeError("warm_transfer_participant_identity_missing")
        async with self._consultation_lock:
            target_identity = self._find_consultation_target()
            async with self._livekit_api() as lkapi:
                if target_identity:
                    try:
                        await _remove_participant(
                            lkapi,
                            room_name=self._ctx.room.name,
                            participant_identity=target_identity,
                        )
                    except Exception:
                        logger.info(
                            "livekit_warm_consult_target_already_absent",
                            extra={"voice_session_id": self._metadata.voice_session_id},
                        )
                await set_bidirectional_hold_subscriptions(
                    lkapi,
                    room_name=self._ctx.room.name,
                    caller_identity=caller_identity,
                    human_identity=human_identity,
                    subscribe=True,
                )
            safe_id = (
                hashlib.sha256(target_identity.encode("utf-8")).hexdigest()[:20]
                if target_identity
                else None
            )
            self._consultation = None
            self._stop_hold_audio()
            return {
                "action": "warm_transfer_cancel",
                "phase": "cancelled",
                "consultation_id": safe_id,
                "customer_restored": True,
            }

    async def _recover_disconnected_consultation(self, identity: str) -> None:
        state = self._consultation
        if state is None or identity not in {
            state.target_identity,
            state.caller_identity,
            state.human_identity,
        }:
            return
        async with self._consultation_lock:
            state = self._consultation
            if state is None:
                return
            try:
                async with self._livekit_api() as lkapi:
                    if identity == state.target_identity:
                        await set_bidirectional_hold_subscriptions(
                            lkapi,
                            room_name=self._ctx.room.name,
                            caller_identity=state.caller_identity,
                            human_identity=state.human_identity,
                            subscribe=True,
                        )
                    else:
                        try:
                            await _remove_participant(
                                lkapi,
                                room_name=self._ctx.room.name,
                                participant_identity=state.target_identity,
                            )
                        except Exception:
                            pass
            except Exception:
                logger.exception(
                    "livekit_warm_consult_disconnect_recovery_failed",
                    extra={"voice_session_id": self._metadata.voice_session_id},
                )
            finally:
                self._consultation = None
                self._stop_hold_audio()
            try:
                await self._client.controller_event(
                    event_type="consultation.failed",
                    room_name=self._ctx.room.name,
                    controller_identity=self._ctx.room.local_participant.identity,
                    role=self._metadata.role,
                    provider_status="failed",
                    provider_reason=(
                        "consultation_target_disconnected"
                        if identity == state.target_identity
                        else "consultation_call_leg_disconnected"
                    ),
                    safe_result={"consultation_id": state.safe_id},
                )
            except Exception:
                logger.warning(
                    "livekit_warm_consult_failure_event_failed",
                    extra={"voice_session_id": self._metadata.voice_session_id},
                )
