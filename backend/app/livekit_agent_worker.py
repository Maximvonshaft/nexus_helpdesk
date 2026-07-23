from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from livekit import api as livekit_api
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    cli,
    inference,
)

from .livekit_agent_config import (
    LiveKitAgentWorkerConfig,
    livekit_agent_registration_name,
    load_livekit_agent_worker_config,
    materialize_livekit_worker_credentials,
)

logger = logging.getLogger("nexus.livekit-agent")
_COMMAND_TOPIC = "nexus.telephony.command.v1"
_ALLOWED_ROLES = {"ai_controller", "controller"}
_ALLOWED_COMMANDS = {"hold", "resume", "keypad", "warm_transfer"}
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


@dataclass(frozen=True)
class AgentJobMetadata:
    role: str
    voice_session_id: str
    conversation_public_id: str
    channel_account_id: int | None = None


def parse_agent_job_metadata(raw: str | None) -> AgentJobMetadata:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("livekit_agent_job_metadata_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("livekit_agent_job_metadata_invalid")
    if value.get("schema") != "nexus.livekit-agent-session.v1":
        raise RuntimeError("livekit_agent_job_schema_invalid")
    role = str(value.get("role") or "").strip().lower()
    voice_session_id = str(value.get("voice_session_id") or "").strip()
    conversation_public_id = str(
        value.get("conversation_public_id") or ""
    ).strip()
    if role not in _ALLOWED_ROLES:
        raise RuntimeError("livekit_agent_role_invalid")
    if not voice_session_id or not conversation_public_id:
        raise RuntimeError("livekit_agent_authority_context_missing")
    raw_account_id = value.get("channel_account_id")
    channel_account_id = (
        int(raw_account_id)
        if isinstance(raw_account_id, int) and raw_account_id > 0
        else None
    )
    return AgentJobMetadata(
        role=role,
        voice_session_id=voice_session_id[:64],
        conversation_public_id=conversation_public_id[:64],
        channel_account_id=channel_account_id,
    )


class NexusRuntimeClient:
    def __init__(self, config: LiveKitAgentWorkerConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.nexus_internal_api_url,
            timeout=httpx.Timeout(config.request_timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def agent_turn(
        self,
        *,
        metadata: AgentJobMetadata,
        turn_id: int,
        transcript: str,
        participant_identity: str | None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/api/telephony/internal/agent-turn",
            headers={
                "Authorization": f"Bearer {self._config.shared_secret}",
                "Content-Type": "application/json",
            },
            json={
                "conversation_id": metadata.conversation_public_id,
                "voice_session_id": metadata.voice_session_id,
                "turn_id": turn_id,
                "transcript": transcript[:2000],
                "stt_language": None,
                "participant_identity": participant_identity,
            },
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("nexus_agent_turn_response_invalid")
        return value

    async def controller_event(
        self,
        *,
        event_type: str,
        room_name: str,
        controller_identity: str | None,
        role: str,
        command_reference: str | None = None,
        provider_status: str | None = None,
        provider_reason: str | None = None,
        safe_result: dict[str, Any] | None = None,
        call_status: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "event_id": f"lka_{uuid.uuid4().hex}",
            "event_type": event_type,
            "room_name": room_name,
            "controller_identity": controller_identity,
            "role": role,
            "command_reference": command_reference,
            "provider_status": provider_status,
            "provider_reason": provider_reason,
            "safe_result": safe_result or {},
            "call_status": call_status,
        }
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signed_at = str(int(time.time()))
        signature = hmac.new(
            self._config.shared_secret.encode("utf-8"),
            signed_at.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        response = await self._client.post(
            "/api/telephony/livekit/controller-events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Nexus-Controller-Timestamp": signed_at,
                "X-Nexus-Controller-Signature": f"sha256={signature}",
            },
        )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {}


class NexusVoiceAgent(Agent):
    """LiveKit speech pipeline whose only reasoning authority is Nexus Runtime."""

    def __init__(
        self,
        *,
        client: NexusRuntimeClient,
        metadata: AgentJobMetadata,
        config: LiveKitAgentWorkerConfig,
        room: rtc.Room,
    ) -> None:
        super().__init__(
            instructions=(
                "You are the LiveKit media adapter for Nexus. Business reasoning, "
                "knowledge, tools, human handoff and ticket decisions are returned by "
                "the canonical Nexus Agent Runtime. Never make an independent decision."
            )
        )
        self._client = client
        self._metadata = metadata
        self._config = config
        self._room = room
        self._turn_id = 0
        self._handoff_wait_announced = False

    async def on_enter(self) -> None:
        await self.session.say(
            self._config.greeting,
            allow_interruptions=True,
            add_to_chat_ctx=False,
        )

    async def llm_node(self, chat_ctx, tools, model_settings=None):
        del tools, model_settings
        transcript = latest_user_text(chat_ctx)
        if not transcript:
            return ""
        self._turn_id += 1
        try:
            result = await self._client.agent_turn(
                metadata=self._metadata,
                turn_id=self._turn_id,
                transcript=transcript,
                participant_identity=_first_remote_identity(self._room),
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.exception(
                "nexus_agent_turn_failed",
                extra={
                    "voice_session_id": self._metadata.voice_session_id,
                    "error_type": type(exc).__name__,
                },
            )
            return (
                "I am temporarily unable to complete that request. "
                "Please stay on the line while support becomes available."
            )
        reply = str(result.get("reply") or "").strip()
        handoff_requested = bool(result.get("handoff_requested"))
        if reply:
            if handoff_requested:
                self._handoff_wait_announced = True
            return reply[:4000]
        if handoff_requested and not self._handoff_wait_announced:
            self._handoff_wait_announced = True
            return self._config.handoff_wait_message
        return ""


def latest_user_text(chat_ctx: Any) -> str:
    items = list(getattr(chat_ctx, "items", []) or [])
    for item in reversed(items):
        if getattr(item, "type", None) != "message":
            continue
        if str(getattr(item, "role", "")).lower() not in {"user", "customer"}:
            continue
        text = str(getattr(item, "text_content", None) or "").strip()
        if text:
            return text[:2000]
    return ""


def parse_controller_command(packet: Any) -> dict[str, Any] | None:
    if str(getattr(packet, "topic", "") or "") != _COMMAND_TOPIC:
        return None
    raw = getattr(packet, "data", b"")
    try:
        value = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema") != _COMMAND_TOPIC:
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


class TelephonyController:
    def __init__(
        self,
        *,
        ctx: JobContext,
        client: NexusRuntimeClient,
        metadata: AgentJobMetadata,
        config: LiveKitAgentWorkerConfig,
        session: AgentSession | None,
        background_audio: BackgroundAudioPlayer,
    ) -> None:
        self._ctx = ctx
        self._client = client
        self._metadata = metadata
        self._config = config
        self._session = session
        self._background_audio = background_audio
        self._hold_handle: Any = None
        self._hold_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()

    def register(self) -> None:
        @self._ctx.room.on("data_received")
        def _on_data(packet: rtc.DataPacket) -> None:
            command = parse_controller_command(packet)
            if command is None:
                return
            task = asyncio.create_task(self._execute(command))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._hold_handle is not None and not self._hold_handle.done():
            self._hold_handle.stop()

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
            return await self._warm_transfer(command)
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
            if held and (self._hold_handle is None or self._hold_handle.done()):
                self._hold_handle = self._background_audio.play(
                    BuiltinAudioClip.HOLD_MUSIC.path(),
                    loop=True,
                )
            try:
                async with livekit_api.LiveKitAPI(
                    url=_server_api_url(os.environ["LIVEKIT_URL"]),
                    api_key=os.environ["LIVEKIT_API_KEY"],
                    api_secret=os.environ["LIVEKIT_API_SECRET"],
                ) as lkapi:
                    counts = await set_bidirectional_hold_subscriptions(
                        lkapi,
                        room_name=self._ctx.room.name,
                        caller_identity=caller_identity,
                        human_identity=human_identity,
                        subscribe=not held,
                    )
            except Exception:
                if held and self._hold_handle is not None:
                    self._hold_handle.stop()
                    self._hold_handle = None
                raise
            if not held and self._hold_handle is not None:
                self._hold_handle.stop()
                self._hold_handle = None
            return {
                "action": "hold" if held else "resume",
                "held": held,
                **counts,
            }

    async def _warm_transfer(self, command: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("agent_session_unavailable_for_warm_transfer")
        target = command.get("target")
        trunk_id = command.get("outbound_trunk_id")
        if not target or not trunk_id:
            raise RuntimeError("warm_transfer_target_or_trunk_missing")
        transfer_model = str(self._config.transfer_llm_model or "").strip()
        if not transfer_model:
            raise RuntimeError("warm_transfer_consultation_model_not_configured")
        from livekit.agents.beta.workflows import WarmTransferTask

        current_agent = getattr(self._session, "current_agent", None)
        chat_ctx = getattr(current_agent, "chat_ctx", None)
        result = await WarmTransferTask(
            sip_call_to=target,
            sip_trunk_id=trunk_id,
            chat_ctx=chat_ctx,
            stt=inference.STT(model=self._config.stt_model),
            llm=inference.LLM(model=transfer_model),
            tts=inference.TTS(model=self._config.tts_model),
            ringing_timeout=30.0,
            extra_instructions=(
                "This is an operational transfer consultation. Brief the human only "
                "with the bounded conversation context and ask whether they can accept. "
                "Do not make business decisions or promise an outcome."
            ),
        )
        identity = str(getattr(result, "human_agent_identity", "") or "")
        return {
            "action": "warm_transfer",
            "human_agent_identity_hash": (
                hashlib.sha256(identity.encode("utf-8")).hexdigest()
                if identity
                else None
            ),
        }


def _bounded_reason(exc: Exception) -> str:
    value = str(exc or type(exc).__name__).strip().lower().replace(" ", "_")
    safe = "".join(
        character
        for character in value
        if character.isalnum() or character in "_-:"
    )
    return (safe or type(exc).__name__.lower())[:160]


async def _heartbeat_loop(
    *,
    client: NexusRuntimeClient,
    ctx: JobContext,
    metadata: AgentJobMetadata,
    interval_seconds: int,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await client.controller_event(
                event_type="controller.heartbeat",
                room_name=ctx.room.name,
                controller_identity=ctx.room.local_participant.identity,
                role=metadata.role,
            )
        except Exception as exc:
            logger.warning(
                "livekit_controller_heartbeat_failed",
                extra={
                    "voice_session_id": metadata.voice_session_id,
                    "error_type": type(exc).__name__,
                },
            )


def _first_remote_identity(room: rtc.Room) -> str | None:
    participants = list(room.remote_participants.values())
    for participant in participants:
        if participant.identity:
            return participant.identity
    return None


server = AgentServer(host="0.0.0.0", port=8081)


@server.rtc_session(agent_name=livekit_agent_registration_name())
async def nexus_livekit_agent(ctx: JobContext) -> None:
    config = load_livekit_agent_worker_config()
    metadata = parse_agent_job_metadata(ctx.job.metadata)
    ctx.log_context_fields = {
        "room": ctx.room.name,
        "voice_session_id": metadata.voice_session_id,
        "role": metadata.role,
    }
    client = NexusRuntimeClient(config)
    session: AgentSession | None = None
    disconnected = asyncio.Event()

    @ctx.room.on("disconnected")
    def _on_disconnected(_reason: Any) -> None:
        disconnected.set()

    if metadata.role == "ai_controller":
        session = AgentSession(
            stt=inference.STT(model=config.stt_model),
            tts=inference.TTS(model=config.tts_model),
            turn_detection=config.turn_detection,
        )
        agent = NexusVoiceAgent(
            client=client,
            metadata=metadata,
            config=config,
            room=ctx.room,
        )
        await session.start(agent=agent, room=ctx.room)
        await ctx.connect()
    else:
        await ctx.connect()

    background_audio = BackgroundAudioPlayer()
    if session is None:
        await background_audio.start(room=ctx.room)
    else:
        await background_audio.start(room=ctx.room, agent_session=session)
    controller = TelephonyController(
        ctx=ctx,
        client=client,
        metadata=metadata,
        config=config,
        session=session,
        background_audio=background_audio,
    )
    controller.register()
    heartbeat_task: asyncio.Task[Any] | None = None
    try:
        await client.controller_event(
            event_type="controller.joined",
            room_name=ctx.room.name,
            controller_identity=ctx.room.local_participant.identity,
            role=metadata.role,
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(
                client=client,
                ctx=ctx,
                metadata=metadata,
                interval_seconds=config.heartbeat_seconds,
            )
        )
        await disconnected.wait()
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        await controller.close()
        await background_audio.aclose()
        try:
            await client.controller_event(
                event_type="controller.left",
                room_name=ctx.room.name,
                controller_identity=ctx.room.local_participant.identity,
                role=metadata.role,
            )
        except Exception:
            logger.warning(
                "livekit_controller_left_event_failed",
                extra={"voice_session_id": metadata.voice_session_id},
            )
        await client.aclose()


if __name__ == "__main__":
    materialize_livekit_worker_credentials()
    load_livekit_agent_worker_config()
    cli.run_app(server)
