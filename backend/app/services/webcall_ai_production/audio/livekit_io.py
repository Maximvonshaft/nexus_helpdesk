from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
import wave
from dataclasses import dataclass
from typing import Protocol

from ..livekit_service import issue_join_token

LOGGER = logging.getLogger(__name__)


class LiveKitIOError(RuntimeError):
    pass


class VisitorDisconnected(LiveKitIOError):
    pass


@dataclass(frozen=True)
class LiveKitMediaTurn:
    audio_bytes: bytes
    sample_rate: int
    channels: int
    mime_type: str = "audio/pcm"
    language: str | None = None

    @property
    def customer_audio(self) -> bytes:
        return self.audio_bytes


@dataclass(frozen=True)
class PCMFrame:
    data: bytes
    sample_rate: int
    channels: int


class LiveKitRTCBackend(Protocol):
    def connect(self, *, url: str, token: str, room_name: str, participant_identity: str) -> None: ...
    def collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float) -> LiveKitMediaTurn: ...
    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None: ...
    def close(self) -> None: ...


class LiveKitAgentIO:
    def __init__(
        self,
        *,
        room_name: str,
        participant_identity: str,
        ttl_seconds: int,
        livekit_url: str | None = None,
        backend: LiveKitRTCBackend | None = None,
    ) -> None:
        self.room_name = room_name
        self.participant_identity = participant_identity
        self.ttl_seconds = ttl_seconds
        self.livekit_url = livekit_url or os.getenv("LIVEKIT_URL")
        if not self.livekit_url:
            raise LiveKitIOError("LIVEKIT_URL is required")
        self.token = issue_join_token(
            room_name=room_name,
            participant_identity=participant_identity,
            ttl_seconds=ttl_seconds,
        )
        self._backend = backend or SDKLiveKitRTCBackend()
        self._connected = False

    def connect(self) -> None:
        started = time.monotonic()
        try:
            self._backend.connect(
                url=self.livekit_url or "",
                token=self.token.participant_token,
                room_name=self.room_name,
                participant_identity=self.participant_identity,
            )
            self._connected = True
            LOGGER.info(
                "webcall_ai_livekit_connected",
                extra={"room_name": self.room_name, "participant_identity": self.participant_identity, "latency_ms": int((time.monotonic() - started) * 1000)},
            )
        except Exception as exc:
            LOGGER.exception("webcall_ai_livekit_connect_failed", extra={"room_name": self.room_name, "participant_identity": self.participant_identity, "error": type(exc).__name__})
            raise LiveKitIOError("livekit_connect_failed") from exc

    def collect_next_customer_utterance(self, *, timeout_seconds: float = 20.0, max_seconds: float = 12.0) -> LiveKitMediaTurn:
        if not self._connected:
            self.connect()
        try:
            return self._backend.collect_next_customer_utterance(timeout_seconds=timeout_seconds, max_seconds=max_seconds)
        except VisitorDisconnected:
            raise
        except Exception as exc:
            LOGGER.exception("webcall_ai_livekit_collect_failed", extra={"room_name": self.room_name, "participant_identity": self.participant_identity, "error": type(exc).__name__})
            raise LiveKitIOError("livekit_collect_failed") from exc

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        if not audio_bytes:
            raise LiveKitIOError("AI audio publication requires non-empty audio bytes")
        if not self._connected:
            self.connect()
        try:
            self._backend.publish_ai_audio(audio_bytes, mime_type=mime_type)
        except Exception as exc:
            LOGGER.exception("webcall_ai_livekit_publish_failed", extra={"room_name": self.room_name, "participant_identity": self.participant_identity, "mime_type": mime_type, "error": type(exc).__name__})
            raise LiveKitIOError("livekit_publish_failed") from exc

    def close(self) -> None:
        try:
            self._backend.close()
        finally:
            self._connected = False


class SDKLiveKitRTCBackend:
    def __init__(self) -> None:
        self._room = None
        self._audio_source = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._audio_queue: asyncio.Queue[PCMFrame | None] | None = None
        self._participant_identity: str | None = None
        self._thread: threading.Thread | None = None

    def connect(self, *, url: str, token: str, room_name: str, participant_identity: str) -> None:
        self._participant_identity = participant_identity
        ready: threading.Event = threading.Event()
        error: list[BaseException] = []

        def run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._connect(url=url, token=token, room_name=room_name))
            except BaseException as exc:
                error.append(exc)
                ready.set()
                return
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run_loop, name=f"webcall-ai-livekit-{participant_identity}", daemon=True)
        self._thread.start()
        if not ready.wait(timeout=float(os.getenv("WEBCALL_AI_LIVEKIT_CONNECT_TIMEOUT_SECONDS", "15"))):
            raise LiveKitIOError("livekit_connect_timeout")
        if error:
            raise error[0]

    async def _connect(self, *, url: str, token: str, room_name: str) -> None:
        try:
            from livekit import rtc
        except Exception as exc:
            raise LiveKitIOError("livekit_sdk_missing") from exc
        self._loop = asyncio.get_running_loop()
        self._audio_queue = asyncio.Queue()
        self._room = rtc.Room()

        @self._room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
                asyncio.run_coroutine_threadsafe(self._drain_audio_track(track), self._loop)

        @self._room.on("participant_disconnected")
        def on_participant_disconnected(participant):
            if self._audio_queue is not None:
                asyncio.run_coroutine_threadsafe(self._audio_queue.put(None), self._loop)

        await self._room.connect(url, token)
        sample_rate = int(os.getenv("WEBCALL_AI_TTS_SAMPLE_RATE", "24000"))
        channels = int(os.getenv("WEBCALL_AI_TTS_CHANNELS", "1"))
        self._audio_source = rtc.AudioSource(sample_rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track("nexusdesk-ai-voice", self._audio_source)
        await self._room.local_participant.publish_track(track)
        LOGGER.info("webcall_ai_livekit_room_joined", extra={"room_name": room_name, "participant_identity": self._participant_identity})

    async def _drain_audio_track(self, track) -> None:
        from livekit import rtc

        stream = rtc.AudioStream(track)
        async for event in stream:
            frame = getattr(event, "frame", event)
            data = getattr(frame, "data", None)
            if data is not None and self._audio_queue is not None:
                sample_rate = int(getattr(frame, "sample_rate", 0) or os.getenv("WEBCALL_AI_AUDIO_SAMPLE_RATE", "48000"))
                channels = int(getattr(frame, "num_channels", 0) or getattr(frame, "channels", 0) or 1)
                await self._audio_queue.put(PCMFrame(data=bytes(data), sample_rate=sample_rate, channels=channels))

    def collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float) -> LiveKitMediaTurn:
        if self._loop is None:
            raise LiveKitIOError("livekit_loop_not_ready")
        future = asyncio.run_coroutine_threadsafe(
            self._collect_next_customer_utterance(timeout_seconds=timeout_seconds, max_seconds=max_seconds),
            self._loop,
        )
        return future.result(timeout=timeout_seconds + max_seconds + 1)

    async def _collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float) -> LiveKitMediaTurn:
        if self._audio_queue is None:
            raise LiveKitIOError("livekit_audio_queue_not_ready")
        chunks: list[bytes] = []
        sample_rate = int(os.getenv("WEBCALL_AI_AUDIO_SAMPLE_RATE", "48000"))
        channels = 1
        deadline = time.monotonic() + max_seconds
        min_seconds = float(os.getenv("WEBCALL_AI_MIN_UTTERANCE_SECONDS", "0.35"))
        silence_end_ms = int(os.getenv("WEBCALL_AI_SILENCE_END_MS", "700"))
        speech_seen = False
        silence_ms = 0
        while time.monotonic() < deadline:
            timeout = min(timeout_seconds, max(0.1, deadline - time.monotonic()))
            frame = await asyncio.wait_for(self._audio_queue.get(), timeout=timeout)
            if frame is None:
                raise VisitorDisconnected("visitor_disconnected")
            sample_rate = frame.sample_rate
            channels = frame.channels
            chunks.append(frame.data)
            frame_ms = _pcm_frame_duration_ms(frame.data, sample_rate=sample_rate, channels=channels)
            if is_speech_pcm16(frame.data):
                speech_seen = True
                silence_ms = 0
            elif speech_seen:
                silence_ms += frame_ms
            utterance_seconds = sum(_pcm_frame_duration_ms(item, sample_rate=sample_rate, channels=channels) for item in chunks) / 1000.0
            if speech_seen and utterance_seconds >= min_seconds and silence_ms >= silence_end_ms:
                break
            if sum(len(item) for item in chunks) >= int(os.getenv("WEBCALL_AI_MAX_UTTERANCE_BYTES", "768000")):
                break
        if not chunks:
            raise LiveKitIOError("customer_audio_timeout")
        return LiveKitMediaTurn(audio_bytes=b"".join(chunks), sample_rate=sample_rate, channels=channels, mime_type="audio/pcm", language=None)

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        if self._loop is None:
            raise LiveKitIOError("livekit_loop_not_ready")
        future = asyncio.run_coroutine_threadsafe(self._publish_ai_audio(audio_bytes, mime_type=mime_type), self._loop)
        future.result(timeout=float(os.getenv("WEBCALL_AI_LIVEKIT_PUBLISH_TIMEOUT_SECONDS", "20")))

    async def _publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        if self._audio_source is None:
            raise LiveKitIOError("livekit_audio_source_not_ready")
        from livekit import rtc

        pcm, sample_rate, channels = decode_audio_for_livekit(audio_bytes, mime_type=mime_type)
        samples_per_channel = max(1, int(sample_rate * 0.02))
        bytes_per_sample = 2
        frame_bytes = samples_per_channel * channels * bytes_per_sample
        for offset in range(0, len(pcm), frame_bytes):
            chunk = pcm[offset : offset + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
            frame = rtc.AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=samples_per_channel,
            )
            await self._audio_source.capture_frame(frame)

    def close(self) -> None:
        if self._room is not None:
            try:
                if self._loop is not None:
                    future = asyncio.run_coroutine_threadsafe(self._room.disconnect(), self._loop)
                    future.result(timeout=5)
            except Exception:
                LOGGER.exception("webcall_ai_livekit_disconnect_failed", extra={"participant_identity": self._participant_identity})
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._room = None
        self._audio_source = None
        self._audio_queue = None
        self._loop = None
        self._thread = None


def decode_audio_for_livekit(audio_bytes: bytes, *, mime_type: str) -> tuple[bytes, int, int]:
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized in {"audio/wav", "audio/x-wav", "audio/wave"} or audio_bytes.startswith(b"RIFF"):
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            pcm = wav.readframes(wav.getnframes())
        if sample_width != 2:
            raise LiveKitIOError("tts_wav_must_be_pcm16")
        return pcm, sample_rate, channels
    if normalized in {"audio/l16", "audio/pcm", "application/octet-stream"}:
        return audio_bytes, int(os.getenv("WEBCALL_AI_TTS_SAMPLE_RATE", "24000")), int(os.getenv("WEBCALL_AI_TTS_CHANNELS", "1"))
    raise LiveKitIOError("unsupported_tts_audio_format")


def pcm16_to_wav(pcm_bytes: bytes, *, sample_rate: int, channels: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return buffer.getvalue()


def is_speech_pcm16(pcm_bytes: bytes, *, threshold: int | None = None) -> bool:
    if not pcm_bytes:
        return False
    threshold_value = threshold if threshold is not None else int(os.getenv("WEBCALL_AI_VAD_ENERGY_THRESHOLD", "350"))
    sample_count = max(1, len(pcm_bytes) // 2)
    total = 0
    for offset in range(0, len(pcm_bytes) - 1, 2):
        sample = int.from_bytes(pcm_bytes[offset : offset + 2], byteorder="little", signed=True)
        total += abs(sample)
    return (total // sample_count) >= threshold_value


def _pcm_frame_duration_ms(pcm_bytes: bytes, *, sample_rate: int, channels: int) -> int:
    if sample_rate <= 0 or channels <= 0:
        return 0
    samples_per_channel = len(pcm_bytes) / 2 / channels
    return int((samples_per_channel / sample_rate) * 1000)
