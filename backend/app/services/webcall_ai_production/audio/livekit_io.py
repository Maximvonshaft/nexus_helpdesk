from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from ..livekit_service import issue_join_token
from .stats import analyze_pcm16_audio

LOGGER = logging.getLogger(__name__)


class LiveKitIOError(RuntimeError):
    pass


class VisitorDisconnected(LiveKitIOError):
    pass


class BargeInInterrupted(LiveKitIOError):
    def __init__(self, *, speech_ms: int, buffered_frames: int) -> None:
        super().__init__("barge_in_interrupted")
        self.speech_ms = speech_ms
        self.buffered_frames = buffered_frames


@dataclass(frozen=True)
class LiveKitMediaTurn:
    audio_bytes: bytes
    sample_rate: int
    channels: int
    mime_type: str = "audio/pcm"
    language: str | None = None
    audio_stats: dict[str, Any] | None = None

    @property
    def customer_audio(self) -> bytes:
        return self.audio_bytes


@dataclass(frozen=True)
class PCMFrame:
    data: bytes
    sample_rate: int
    channels: int
    track_sid: str | None = None
    participant_identity: str | None = None
    muted: bool = False


class LiveKitRTCBackend(Protocol):
    def connect(self, *, url: str, token: str, room_name: str, participant_identity: str) -> None: ...
    def collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float) -> LiveKitMediaTurn: ...
    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None: ...
    def publish_ai_audio_stream(self, chunks: Iterable[Any], *, mime_type: str) -> None: ...
    def cancel_ai_audio_stream(self, *, reason: str) -> None: ...
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
        telemetry_callback: Callable[[str, dict[str, Any]], None] | None = None,
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
        self._backend = backend or SDKLiveKitRTCBackend(telemetry_callback=telemetry_callback)
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

    def audio_ingress_snapshot(self) -> dict[str, Any] | None:
        snapshot = getattr(self._backend, "audio_ingress_snapshot", None)
        if callable(snapshot):
            return snapshot()
        return None

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        if not audio_bytes:
            raise LiveKitIOError("AI audio publication requires non-empty audio bytes")
        if not self._connected:
            self.connect()
        try:
            self._backend.publish_ai_audio(audio_bytes, mime_type=mime_type)
        except BargeInInterrupted:
            self.cancel_ai_audio_stream(reason="barge_in")
            raise
        except Exception as exc:
            LOGGER.exception("webcall_ai_livekit_publish_failed", extra={"room_name": self.room_name, "participant_identity": self.participant_identity, "mime_type": mime_type, "error": type(exc).__name__})
            raise LiveKitIOError("livekit_publish_failed") from exc

    def publish_ai_audio_stream(self, chunks: Iterable[Any], *, mime_type: str) -> None:
        if not self._connected:
            self.connect()
        try:
            if hasattr(self._backend, "publish_ai_audio_stream"):
                self._backend.publish_ai_audio_stream(chunks, mime_type=mime_type)
                return
            chunk_list = tuple(chunks)
            if not chunk_list:
                raise LiveKitIOError("AI audio stream publication requires at least one chunk")
            audio = b"".join(bytes(getattr(chunk, "audio_bytes", b"") or b"") for chunk in chunk_list)
            self._backend.publish_ai_audio(audio, mime_type=mime_type)
        except BargeInInterrupted:
            self.cancel_ai_audio_stream(reason="barge_in")
            raise
        except Exception as exc:
            LOGGER.exception("webcall_ai_livekit_stream_publish_failed", extra={"room_name": self.room_name, "participant_identity": self.participant_identity, "mime_type": mime_type, "error": type(exc).__name__})
            raise LiveKitIOError("livekit_stream_publish_failed") from exc

    def cancel_ai_audio_stream(self, *, reason: str) -> None:
        if hasattr(self._backend, "cancel_ai_audio_stream"):
            self._backend.cancel_ai_audio_stream(reason=reason)

    def close(self) -> None:
        try:
            self._backend.close()
        finally:
            self._connected = False


class SDKLiveKitRTCBackend:
    def __init__(self, *, telemetry_callback: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self._room = None
        self._audio_source = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._audio_queue: asyncio.Queue[PCMFrame | None] | None = None
        self._barge_in_buffer: list[PCMFrame] = []
        self._participant_identity: str | None = None
        self._thread: threading.Thread | None = None
        self._telemetry_callback = telemetry_callback
        self._remote_audio_track_seen = False
        self._remote_audio_track_muted = False
        self._remote_track_sid: str | None = None
        self._remote_participant_identity: str | None = None

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
                self._record_remote_track_subscribed(track=track, publication=publication, participant=participant)
                asyncio.run_coroutine_threadsafe(self._drain_audio_track(track, publication=publication, participant=participant), self._loop)

        @self._room.on("track_muted")
        def on_track_muted(publication, participant):
            if self._track_sid(publication=publication) == self._remote_track_sid:
                self._remote_audio_track_muted = True

        @self._room.on("track_unmuted")
        def on_track_unmuted(publication, participant):
            if self._track_sid(publication=publication) == self._remote_track_sid:
                self._remote_audio_track_muted = False

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

    async def _drain_audio_track(self, track, *, publication=None, participant=None) -> None:
        from livekit import rtc

        stream = rtc.AudioStream(track)
        track_sid = self._track_sid(track=track, publication=publication)
        participant_identity = self._participant_identity_value(participant)
        async for event in stream:
            frame = getattr(event, "frame", event)
            data = getattr(frame, "data", None)
            if data is not None and self._audio_queue is not None:
                sample_rate = int(getattr(frame, "sample_rate", 0) or os.getenv("WEBCALL_AI_AUDIO_SAMPLE_RATE", "48000"))
                channels = int(getattr(frame, "num_channels", 0) or getattr(frame, "channels", 0) or 1)
                await self._audio_queue.put(
                    PCMFrame(
                        data=bytes(data),
                        sample_rate=sample_rate,
                        channels=channels,
                        track_sid=track_sid,
                        participant_identity=participant_identity,
                        muted=self._remote_audio_track_muted,
                    )
                )

    def collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float) -> LiveKitMediaTurn:
        if self._loop is None:
            raise LiveKitIOError("livekit_loop_not_ready")
        future = asyncio.run_coroutine_threadsafe(
            self._collect_next_customer_utterance(timeout_seconds=timeout_seconds, max_seconds=max_seconds),
            self._loop,
        )
        return future.result(timeout=timeout_seconds + max_seconds + 1)

    def audio_ingress_snapshot(self) -> dict[str, Any]:
        stats = analyze_pcm16_audio(
            b"",
            sample_rate=int(os.getenv("WEBCALL_AI_AUDIO_SAMPLE_RATE", "48000")),
            channels=1,
            frame_count=0,
            remote_track_seen=self._remote_audio_track_seen,
            audio_track_muted=self._remote_audio_track_muted,
        ).as_payload()
        stats.update(
            {
                "participant_identity": self._remote_participant_identity,
                "track_sid": self._remote_track_sid,
                "remote_track_seen": self._remote_audio_track_seen,
                "audio_track_muted": self._remote_audio_track_muted,
            }
        )
        return stats

    async def _collect_next_customer_utterance(self, *, timeout_seconds: float, max_seconds: float) -> LiveKitMediaTurn:
        if self._audio_queue is None:
            raise LiveKitIOError("livekit_audio_queue_not_ready")
        chunks: list[bytes] = []
        sample_rate = int(os.getenv("WEBCALL_AI_AUDIO_SAMPLE_RATE", "48000"))
        channels = 1
        track_sid: str | None = self._remote_track_sid
        participant_identity: str | None = self._remote_participant_identity
        frame_count = 0
        muted_frames = 0
        max_audio_ms = _max_utterance_audio_ms(max_seconds=max_seconds)
        min_audio_ms = min(_min_utterance_audio_ms(), max_audio_ms)
        silence_end_ms = _silence_end_ms()
        deadline = time.monotonic() + max(max_seconds, max_audio_ms / 1000.0)
        speech_seen = False
        silence_ms = 0
        audio_ms = 0
        pcm_bytes = 0
        end_reason = "deadline"
        while time.monotonic() < deadline:
            timeout = min(timeout_seconds, max(0.1, deadline - time.monotonic()))
            frame = self._pop_buffered_audio_frame()
            if frame is None:
                frame = await asyncio.wait_for(self._audio_queue.get(), timeout=timeout)
            if frame is None:
                raise VisitorDisconnected("visitor_disconnected")
            sample_rate = frame.sample_rate
            channels = frame.channels
            track_sid = frame.track_sid or track_sid
            participant_identity = frame.participant_identity or participant_identity
            frame_count += 1
            muted_frames += 1 if frame.muted else 0
            chunks.append(frame.data)
            frame_ms = _pcm_frame_duration_ms(frame.data, sample_rate=sample_rate, channels=channels)
            audio_ms += frame_ms
            pcm_bytes += len(frame.data)
            if is_speech_pcm16(frame.data):
                speech_seen = True
                silence_ms = 0
            elif speech_seen:
                silence_ms += frame_ms
            if pcm_bytes >= int(os.getenv("WEBCALL_AI_MAX_UTTERANCE_BYTES", "768000")):
                end_reason = "max_utterance_bytes"
                break
            if audio_ms >= max_audio_ms:
                end_reason = "max_utterance_audio_ms"
                break
            if speech_seen and audio_ms >= min_audio_ms and silence_ms >= silence_end_ms:
                end_reason = "silence_after_min_utterance"
                break
        if not chunks:
            raise LiveKitIOError("customer_audio_timeout")
        audio = b"".join(chunks)
        stats = analyze_pcm16_audio(
            audio,
            sample_rate=sample_rate,
            channels=channels,
            frame_count=frame_count,
            remote_track_seen=self._remote_audio_track_seen,
            audio_track_muted=frame_count > 0 and muted_frames == frame_count,
        ).as_payload()
        stats.update(
            {
                "participant_identity": participant_identity,
                "track_sid": track_sid,
                "remote_track_seen": self._remote_audio_track_seen,
                "audio_track_muted": frame_count > 0 and muted_frames == frame_count,
                "capture_mode": "tracking_long",
                "capture_min_audio_ms": min_audio_ms,
                "capture_max_audio_ms": max_audio_ms,
                "capture_silence_end_ms": silence_end_ms,
                "capture_end_reason": end_reason,
            }
        )
        return LiveKitMediaTurn(audio_bytes=audio, sample_rate=sample_rate, channels=channels, mime_type="audio/pcm", language=None, audio_stats=stats)

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        if self._loop is None:
            raise LiveKitIOError("livekit_loop_not_ready")
        future = asyncio.run_coroutine_threadsafe(self._publish_ai_audio(audio_bytes, mime_type=mime_type), self._loop)
        future.result(timeout=float(os.getenv("WEBCALL_AI_LIVEKIT_PUBLISH_TIMEOUT_SECONDS", "20")))

    def publish_ai_audio_stream(self, chunks: Iterable[Any], *, mime_type: str) -> None:
        if self._loop is None:
            raise LiveKitIOError("livekit_loop_not_ready")
        published = False
        timeout = float(os.getenv("WEBCALL_AI_LIVEKIT_PUBLISH_TIMEOUT_SECONDS", "20"))
        for chunk in chunks:
            audio = bytes(getattr(chunk, "audio_bytes", b"") or b"")
            if not audio:
                continue
            published = True
            future = asyncio.run_coroutine_threadsafe(self._publish_ai_audio_chunk(chunk, mime_type=mime_type), self._loop)
            future.result(timeout=timeout)
        if not published:
            raise LiveKitIOError("AI audio stream publication requires at least one chunk")

    async def _publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        if self._audio_source is None:
            raise LiveKitIOError("livekit_audio_source_not_ready")
        pcm, sample_rate, channels = decode_audio_for_livekit(audio_bytes, mime_type=mime_type)
        await self._capture_pcm_frames(pcm, sample_rate=sample_rate, channels=channels)

    async def _publish_ai_audio_stream(self, chunks: tuple[Any, ...], *, mime_type: str) -> None:
        if self._audio_source is None:
            raise LiveKitIOError("livekit_audio_source_not_ready")
        for chunk in chunks:
            await self._publish_ai_audio_chunk(chunk, mime_type=mime_type)

    async def _publish_ai_audio_chunk(self, chunk: Any, *, mime_type: str) -> None:
        if self._audio_source is None:
            raise LiveKitIOError("livekit_audio_source_not_ready")
        audio = bytes(getattr(chunk, "audio_bytes", b"") or b"")
        if not audio:
            return
        chunk_mime = str(getattr(chunk, "mime_type", None) or mime_type)
        normalized = chunk_mime.split(";")[0].strip().lower()
        if normalized in {"audio/l16", "audio/pcm", "application/octet-stream"}:
            sample_rate = int(getattr(chunk, "sample_rate", 0) or os.getenv("WEBCALL_AI_TTS_SAMPLE_RATE", "24000"))
            channels = int(getattr(chunk, "channels", 0) or os.getenv("WEBCALL_AI_TTS_CHANNELS", "1"))
            await self._capture_pcm_frames(audio, sample_rate=sample_rate, channels=channels)
            return
        pcm, sample_rate, channels = decode_audio_for_livekit(audio, mime_type=chunk_mime)
        await self._capture_pcm_frames(pcm, sample_rate=sample_rate, channels=channels)

    async def _capture_pcm_frames(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        from livekit import rtc

        samples_per_channel = max(1, int(sample_rate * 0.02))
        bytes_per_sample = 2
        frame_bytes = samples_per_channel * channels * bytes_per_sample
        barge_in_speech_ms = 0
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
            barge_in_speech_ms = self._raise_if_barge_in_detected(barge_in_speech_ms)

    def cancel_ai_audio_stream(self, *, reason: str) -> None:
        LOGGER.info("webcall_ai_livekit_audio_stream_cancelled", extra={"participant_identity": self._participant_identity, "reason": reason})

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
        self._barge_in_buffer = []
        self._loop = None
        self._thread = None
        self._remote_audio_track_seen = False
        self._remote_audio_track_muted = False
        self._remote_track_sid = None
        self._remote_participant_identity = None

    def _pop_buffered_audio_frame(self) -> PCMFrame | None:
        if not self._barge_in_buffer:
            return None
        return self._barge_in_buffer.pop(0)

    def _raise_if_barge_in_detected(self, speech_ms: int) -> int:
        if not _barge_in_enabled() or self._audio_queue is None:
            return speech_ms
        current_speech_ms = speech_ms
        drained_frames = 0
        while True:
            try:
                frame = self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                return current_speech_ms
            if frame is None:
                raise VisitorDisconnected("visitor_disconnected")
            self._barge_in_buffer.append(frame)
            drained_frames += 1
            frame_ms = _pcm_frame_duration_ms(frame.data, sample_rate=frame.sample_rate, channels=frame.channels)
            if is_speech_pcm16(frame.data, threshold=_barge_in_energy_threshold()):
                current_speech_ms += frame_ms
            else:
                current_speech_ms = 0
            if current_speech_ms >= _barge_in_min_speech_ms():
                raise BargeInInterrupted(speech_ms=current_speech_ms, buffered_frames=len(self._barge_in_buffer) or drained_frames)

    def _record_remote_track_subscribed(self, *, track=None, publication=None, participant=None) -> None:
        self._remote_audio_track_seen = True
        self._remote_audio_track_muted = bool(getattr(publication, "muted", False) or getattr(track, "muted", False))
        self._remote_track_sid = self._track_sid(track=track, publication=publication)
        self._remote_participant_identity = self._participant_identity_value(participant)
        self._emit_telemetry(
            "webcall_ai.livekit.remote_track_subscribed",
            {
                "participant_identity": self._remote_participant_identity,
                "track_sid": self._remote_track_sid,
                "track_kind": "audio",
                "track_muted": self._remote_audio_track_muted,
            },
        )

    def _emit_telemetry(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self._telemetry_callback:
            return
        try:
            self._telemetry_callback(event_type, payload)
        except Exception:
            LOGGER.exception("webcall_ai_livekit_telemetry_failed", extra={"event_type": event_type, "participant_identity": self._participant_identity})

    @staticmethod
    def _track_sid(*, track=None, publication=None) -> str | None:
        value = getattr(publication, "sid", None) or getattr(publication, "track_sid", None) or getattr(track, "sid", None)
        return str(value)[:160] if value else None

    @staticmethod
    def _participant_identity_value(participant=None) -> str | None:
        value = getattr(participant, "identity", None) or getattr(participant, "sid", None)
        return str(value)[:160] if value else None


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


def _min_utterance_audio_ms() -> int:
    return _bounded_int_env("WEBCALL_AI_MIN_UTTERANCE_AUDIO_MS", 4000, minimum=0, maximum=30000)


def _max_utterance_audio_ms(*, max_seconds: float) -> int:
    env_max_ms = _bounded_int_env("WEBCALL_AI_MAX_UTTERANCE_AUDIO_MS", 12000, minimum=1000, maximum=60000)
    caller_max_ms = int(max(0.1, max_seconds) * 1000)
    return max(100, min(env_max_ms, caller_max_ms))


def _silence_end_ms() -> int:
    return _bounded_int_env("WEBCALL_AI_SILENCE_END_MS", 1500, minimum=0, maximum=8000)


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _barge_in_enabled() -> bool:
    return (os.getenv("WEBCALL_AI_BARGE_IN_ENABLED") or "true").strip().lower() in {"1", "true", "yes", "on"}


def _barge_in_min_speech_ms() -> int:
    try:
        return max(40, min(int(os.getenv("WEBCALL_AI_BARGE_IN_MIN_SPEECH_MS", "900")), 3000))
    except ValueError:
        return 900


def _barge_in_energy_threshold() -> int:
    try:
        return max(1, min(int(os.getenv("WEBCALL_AI_BARGE_IN_ENERGY_THRESHOLD", os.getenv("WEBCALL_AI_VAD_ENERGY_THRESHOLD", "350"))), 32000))
    except ValueError:
        return 350
