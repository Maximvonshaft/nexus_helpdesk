from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    BackgroundAudioPlayer,
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
from .livekit_telephony_controller import (
    TelephonyController,
    parse_controller_command,
    publish_dtmf_sequence,
    set_bidirectional_hold_subscriptions,
)

logger = logging.getLogger("nexus.livekit-agent")
_ALLOWED_ROLES = {"ai_controller", "controller"}
_ALLOWED_POLICIES = {"disabled", "notice", "explicit_consent"}
_COMPLIANCE_CAPABILITIES = ("recording", "transcript_persistence")
_DTMF_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class ComplianceRequirement:
    capability: str
    policy: str
    policy_version: str
    prompt: str | None
    prompt_sha256: str


@dataclass(frozen=True)
class AgentJobMetadata:
    role: str
    voice_session_id: str
    conversation_public_id: str
    channel_account_id: int | None = None
    media_origin: str = "unknown"
    compliance: tuple[ComplianceRequirement, ...] = ()


def _clean(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _prompt_sha256(prompt: str | None) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def _parse_compliance(value: Any) -> tuple[ComplianceRequirement, ...]:
    if value in (None, {}):
        return ()
    if not isinstance(value, dict):
        raise RuntimeError("livekit_agent_compliance_invalid")
    if value.get("schema") != "nexus.voice-compliance-policy.v1":
        raise RuntimeError("livekit_agent_compliance_schema_invalid")
    bundle_version = _clean(value.get("policy_version"), limit=80)
    if not bundle_version:
        raise RuntimeError("livekit_agent_compliance_version_missing")
    requirements: list[ComplianceRequirement] = []
    for capability in _COMPLIANCE_CAPABILITIES:
        item = value.get(capability)
        if not isinstance(item, dict):
            raise RuntimeError("livekit_agent_compliance_capability_missing")
        observed_capability = _clean(item.get("capability"), limit=32)
        policy = _clean(item.get("policy"), limit=32).lower()
        policy_version = _clean(item.get("policy_version"), limit=80)
        prompt = _clean(item.get("prompt"), limit=1000) or None
        prompt_sha256 = _clean(item.get("prompt_sha256"), limit=64).lower()
        if observed_capability != capability:
            raise RuntimeError("livekit_agent_compliance_capability_invalid")
        if policy not in _ALLOWED_POLICIES:
            raise RuntimeError("livekit_agent_compliance_policy_invalid")
        if policy_version != bundle_version:
            raise RuntimeError("livekit_agent_compliance_version_mismatch")
        if prompt_sha256 != _prompt_sha256(prompt):
            raise RuntimeError("livekit_agent_compliance_prompt_digest_mismatch")
        if policy != "disabled" and not prompt:
            raise RuntimeError("livekit_agent_compliance_prompt_missing")
        requirements.append(
            ComplianceRequirement(
                capability=capability,
                policy=policy,
                policy_version=policy_version,
                prompt=prompt,
                prompt_sha256=prompt_sha256,
            )
        )
    return tuple(requirements)


def parse_agent_job_metadata(raw: str | None) -> AgentJobMetadata:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("livekit_agent_job_metadata_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("livekit_agent_job_metadata_invalid")
    if value.get("schema") != "nexus.livekit-agent-session.v1":
        raise RuntimeError("livekit_agent_job_schema_invalid")
    role = _clean(value.get("role"), limit=40).lower()
    voice_session_id = _clean(value.get("voice_session_id"), limit=64)
    conversation_public_id = _clean(
        value.get("conversation_public_id"),
        limit=64,
    )
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
    explicit_origin = _clean(value.get("media_origin"), limit=20).lower()
    media_origin = explicit_origin or (
        "browser"
        if value.get("tenant_key")
        else ("sip" if value.get("tenant_id") else "unknown")
    )
    if media_origin not in {"browser", "sip", "unknown"}:
        raise RuntimeError("livekit_agent_media_origin_invalid")
    return AgentJobMetadata(
        role=role,
        voice_session_id=voice_session_id,
        conversation_public_id=conversation_public_id,
        channel_account_id=channel_account_id,
        media_origin=media_origin,
        compliance=_parse_compliance(value.get("compliance")),
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


class NexusControllerAgent(Agent):
    """TTS-capable controller with no business reasoning authority."""

    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a deterministic LiveKit media controller. Never answer a "
                "customer request and never perform business reasoning."
            )
        )

    async def llm_node(self, chat_ctx, tools, model_settings=None):
        del chat_ctx, tools, model_settings
        return ""


class NexusVoiceAgent(Agent):
    """LiveKit speech pipeline whose only reasoning authority is Nexus Runtime."""

    def __init__(
        self,
        *,
        client: NexusRuntimeClient,
        metadata: AgentJobMetadata,
        config: LiveKitAgentWorkerConfig,
        room: rtc.Room,
        compliance_ready: asyncio.Event,
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
        self._compliance_ready = compliance_ready
        self._turn_id = 0
        self._handoff_wait_announced = False

    async def llm_node(self, chat_ctx, tools, model_settings=None):
        del tools, model_settings
        if not self._compliance_ready.is_set():
            return ""
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


class SIPComplianceController:
    """Deterministic notice/DTMF compliance flow for the SIP caller leg."""

    def __init__(
        self,
        *,
        room: rtc.Room,
        session: AgentSession,
        client: NexusRuntimeClient,
        metadata: AgentJobMetadata,
    ) -> None:
        self._room = room
        self._session = session
        self._client = client
        self._metadata = metadata
        self._pending: asyncio.Future[tuple[str, str | None]] | None = None
        self._closed = False

    def register(self) -> None:
        @self._room.on("sip_dtmf_received")
        def _on_dtmf(event: rtc.SipDTMF) -> None:
            pending = self._pending
            if pending is None or pending.done() or event.digit not in {"1", "2"}:
                return
            participant_identity = (
                str(event.participant.identity)
                if event.participant is not None
                else None
            )
            pending.set_result((event.digit, participant_identity))

    async def close(self) -> None:
        self._closed = True
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()

    async def run(self) -> None:
        if self._metadata.media_origin != "sip":
            return
        for requirement in self._metadata.compliance:
            if self._closed or requirement.policy == "disabled":
                continue
            await self._run_requirement(requirement)

    async def _run_requirement(self, requirement: ComplianceRequirement) -> None:
        decision = "timeout"
        participant_identity: str | None = None
        if requirement.policy == "notice":
            handle = self._session.say(
                str(requirement.prompt),
                allow_interruptions=False,
                add_to_chat_ctx=False,
            )
            await handle.wait_for_playout()
            decision = "notice_delivered"
        else:
            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            handle = self._session.say(
                f"{requirement.prompt} Press 1 to accept or 2 to decline.",
                allow_interruptions=False,
                add_to_chat_ctx=False,
            )
            await handle.wait_for_playout()
            try:
                digit, participant_identity = await asyncio.wait_for(
                    self._pending,
                    timeout=_DTMF_TIMEOUT_SECONDS,
                )
                decision = "accepted" if digit == "1" else "declined"
            except asyncio.TimeoutError:
                decision = "timeout"
            finally:
                self._pending = None
        idempotency_key = (
            f"sip-compliance:{self._metadata.voice_session_id}:"
            f"{requirement.capability}:{requirement.policy_version}:"
            f"{requirement.prompt_sha256}"
        )[:180]
        try:
            await self._client.controller_event(
                event_type="compliance.evidence",
                room_name=self._room.name,
                controller_identity=self._room.local_participant.identity,
                role=self._metadata.role,
                provider_status="succeeded",
                safe_result={
                    "capability": requirement.capability,
                    "policy": requirement.policy,
                    "policy_version": requirement.policy_version,
                    "prompt_sha256": requirement.prompt_sha256,
                    "decision": decision,
                    "idempotency_key": idempotency_key,
                    "participant_identity": participant_identity,
                },
            )
        except Exception as exc:
            logger.exception(
                "livekit_compliance_evidence_failed",
                extra={
                    "voice_session_id": self._metadata.voice_session_id,
                    "capability": requirement.capability,
                    "error_type": type(exc).__name__,
                },
            )


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


server = AgentServer(host="127.0.0.1", port=8081)


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
    compliance_ready = asyncio.Event()
    disconnected = asyncio.Event()

    @ctx.room.on("disconnected")
    def _on_disconnected(_reason: Any) -> None:
        disconnected.set()

    session = AgentSession(
        stt=inference.STT(model=config.stt_model),
        tts=inference.TTS(model=config.tts_model),
        turn_detection=config.turn_detection,
    )
    agent: Agent
    if metadata.role == "ai_controller":
        agent = NexusVoiceAgent(
            client=client,
            metadata=metadata,
            config=config,
            room=ctx.room,
            compliance_ready=compliance_ready,
        )
    else:
        agent = NexusControllerAgent()
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()

    background_audio = BackgroundAudioPlayer()
    await background_audio.start(room=ctx.room, agent_session=session)
    controller = TelephonyController(
        ctx=ctx,
        client=client,
        metadata=metadata,
        background_audio=background_audio,
    )
    controller.register()
    compliance = SIPComplianceController(
        room=ctx.room,
        session=session,
        client=client,
        metadata=metadata,
    )
    compliance.register()
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
        await compliance.run()
        compliance_ready.set()
        if metadata.role == "ai_controller":
            await session.say(
                config.greeting,
                allow_interruptions=True,
                add_to_chat_ctx=False,
            )
        elif metadata.media_origin == "sip":
            await session.say(
                config.handoff_wait_message,
                allow_interruptions=True,
                add_to_chat_ctx=False,
            )
        await disconnected.wait()
    finally:
        compliance_ready.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        await compliance.close()
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
