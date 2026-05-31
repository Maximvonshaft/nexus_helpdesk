from __future__ import annotations

import io
import json
import os
import time
import urllib.parse
import wave
from dataclasses import dataclass, field
from typing import Any

from ..audio.livekit_io import PCMFrame
from .base import ProviderError, STTProvider, STTResult
from .http_utils import read_secret_file
from ..metrics import record_webcall_ai_stage
from .streaming_stt_base import STTEvent, final_transcript_from_events

_DEFAULT_DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


class DeepgramStreamingSTTProvider(STTProvider):
    provider_name = "deepgram_streaming"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint or os.getenv("STT_ENDPOINT") or _DEFAULT_DEEPGRAM_URL
        self.token_file = token_file or os.getenv("STT_API_KEY_FILE")

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        mime_type: str | None = None,
    ) -> STTResult:
        pcm, resolved_sample_rate, resolved_channels = prepare_streaming_pcm16(
            audio,
            sample_rate=sample_rate,
            channels=channels,
            mime_type=mime_type,
        )
        session = DeepgramStreamingSTTSession(endpoint=self.endpoint, token_file=self.token_file)
        events: list[STTEvent] = []
        try:
            session.start(language=language, sample_rate=resolved_sample_rate, channels=resolved_channels)
            for frame in iter_pcm_frames(pcm, sample_rate=resolved_sample_rate, channels=resolved_channels):
                session.send_pcm_frame(frame)
                events.extend(session.poll_events())
            events.extend(session.finalize())
        finally:
            session.close()

        text, event_language, confidence = final_transcript_from_events(events)
        if not text:
            raise ProviderError(self.provider_name, "stt_empty_transcript", "Streaming STT returned no transcript")
        return STTResult(
            text=text,
            language=event_language or language or "en",
            confidence=confidence,
            provider_name=self.provider_name,
        )


@dataclass
class DeepgramStreamingSTTSession:
    endpoint: str | None = None
    token_file: str | None = None
    provider_name: str = DeepgramStreamingSTTProvider.provider_name
    _ws: Any = field(default=None, init=False, repr=False)
    _started_at: float | None = field(default=None, init=False)
    _provider_session_id: str | None = field(default=None, init=False)
    _sample_rate: int | None = field(default=None, init=False)
    _channels: int | None = field(default=None, init=False)
    _first_partial_recorded: bool = field(default=False, init=False)

    def start(self, *, language: str | None = None, sample_rate: int, channels: int) -> None:
        if self._ws is not None:
            return
        endpoint = (self.endpoint or _DEFAULT_DEEPGRAM_URL).strip()
        if not endpoint:
            raise ProviderError(self.provider_name, "endpoint_required", "provider endpoint is required")
        token = read_secret_file(self.token_file, provider=self.provider_name)
        self._sample_rate = sample_rate
        self._channels = channels
        self._started_at = time.monotonic()
        self._ws = websocket_connect(
            _url_with_query(endpoint, _deepgram_query(language=language, sample_rate=sample_rate, channels=channels)),
            additional_headers={"Authorization": f"Token {token}"},
            open_timeout=float(os.getenv("STT_CONNECT_TIMEOUT_SECONDS", "5")),
            close_timeout=float(os.getenv("STT_CLOSE_TIMEOUT_SECONDS", "3")),
        )

    def send_pcm_frame(self, frame: PCMFrame) -> None:
        if self._ws is None:
            raise ProviderError(self.provider_name, "stt_stream_not_started", "Streaming STT session has not started")
        self._ws.send(frame.data)

    def poll_events(self) -> list[STTEvent]:
        return self._drain(timeout=float(os.getenv("STT_EVENT_POLL_TIMEOUT_SECONDS", "0.001")))

    def finalize(self) -> list[STTEvent]:
        if self._ws is None:
            return []
        self._ws.send(json.dumps({"type": "Finalize"}))
        deadline = time.monotonic() + float(os.getenv("STT_FINAL_TIMEOUT_SECONDS", "5"))
        events: list[STTEvent] = []
        while time.monotonic() < deadline:
            drained = self._drain(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            events.extend(drained)
            if any(event.speech_final for event in drained):
                break
        return events

    def close(self) -> None:
        if self._ws is None:
            return
        try:
            self._ws.close()
        finally:
            self._ws = None

    def _drain(self, *, timeout: float) -> list[STTEvent]:
        if self._ws is None:
            return []
        events: list[STTEvent] = []
        while True:
            try:
                raw = self._ws.recv(timeout=timeout)
            except TimeoutError:
                break
            except Exception:
                break
            if raw is None:
                break
            event = parse_deepgram_event(raw, provider=self.provider_name, provider_session_id=self._provider_session_id)
            if event is None:
                continue
            if event.provider_session_id:
                self._provider_session_id = event.provider_session_id
            self._record_event_metric(event)
            events.append(event)
            timeout = 0
        return events

    def _record_event_metric(self, event: STTEvent) -> None:
        if self._started_at is None:
            return
        elapsed_ms = int((time.monotonic() - self._started_at) * 1000)
        if event.type == "partial" and not self._first_partial_recorded:
            self._first_partial_recorded = True
            record_webcall_ai_stage(stage="stt_first_partial", provider=self.provider_name, elapsed_ms=elapsed_ms)


def prepare_streaming_pcm16(
    audio: bytes,
    *,
    sample_rate: int | None,
    channels: int | None,
    mime_type: str | None,
) -> tuple[bytes, int, int]:
    if not audio:
        raise ProviderError(DeepgramStreamingSTTProvider.provider_name, "stt_audio_required", "STT requires audio")
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized in {"audio/wav", "audio/x-wav", "audio/wave"} or audio.startswith(b"RIFF"):
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if wav.getsampwidth() != 2:
                raise ProviderError(DeepgramStreamingSTTProvider.provider_name, "stt_wav_must_be_pcm16", "Streaming STT requires PCM16 WAV")
            return wav.readframes(wav.getnframes()), wav.getframerate(), wav.getnchannels()
    if normalized in {"audio/pcm", "audio/l16", "application/octet-stream", ""}:
        if not sample_rate or not channels:
            raise ProviderError(DeepgramStreamingSTTProvider.provider_name, "stt_pcm_metadata_required", "raw PCM requires sample_rate and channels")
        return audio, sample_rate, channels
    raise ProviderError(DeepgramStreamingSTTProvider.provider_name, "stt_streaming_unsupported_audio_format", "Streaming STT requires PCM16 audio")


def iter_pcm_frames(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
    frame_ms: int | None = None,
) -> list[PCMFrame]:
    frame_duration_ms = frame_ms or int(os.getenv("STT_STREAM_FRAME_MS", "20"))
    bytes_per_sample = 2
    frame_size = max(1, int(sample_rate * (frame_duration_ms / 1000.0)) * channels * bytes_per_sample)
    return [
        PCMFrame(data=pcm[offset : offset + frame_size], sample_rate=sample_rate, channels=channels)
        for offset in range(0, len(pcm), frame_size)
        if pcm[offset : offset + frame_size]
    ]


def parse_deepgram_event(raw: str | bytes, *, provider: str, provider_session_id: str | None = None) -> STTEvent | None:
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw_type = str(payload.get("type") or "")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    request_id = str(metadata.get("request_id") or payload.get("request_id") or provider_session_id or "") or None
    if raw_type == "SpeechStarted":
        return STTEvent(type="speech_started", provider=provider, provider_session_id=request_id, raw_type=raw_type)
    if raw_type == "UtteranceEnd":
        return STTEvent(type="speech_ended", provider=provider, provider_session_id=request_id, raw_type=raw_type, speech_final=True)

    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    alternatives = channel.get("alternatives") if isinstance(channel.get("alternatives"), list) else []
    first = alternatives[0] if alternatives and isinstance(alternatives[0], dict) else {}
    transcript = str(first.get("transcript") or "").strip()
    if not transcript:
        return None
    is_final = payload.get("is_final") is True
    speech_final = payload.get("speech_final") is True
    confidence = _confidence_to_percent(first.get("confidence"))
    language = str(payload.get("language") or "").strip() or None
    return STTEvent(
        type="final" if is_final else "partial",
        text=transcript,
        language=language,
        confidence=confidence,
        provider=provider,
        provider_session_id=request_id,
        is_final=is_final,
        speech_final=speech_final,
        raw_type=raw_type or "Results",
    )


def _deepgram_query(*, language: str | None, sample_rate: int, channels: int) -> dict[str, str]:
    query = {
        "model": os.getenv("STT_MODEL", "nova-3"),
        "encoding": "linear16",
        "sample_rate": str(sample_rate),
        "channels": str(channels),
        "interim_results": _bool_query(os.getenv("STT_INTERIM_RESULTS", "true")),
        "endpointing": os.getenv("STT_ENDPOINTING_MS", "300"),
        "punctuate": _bool_query(os.getenv("STT_PUNCTUATE", "true")),
        "smart_format": _bool_query(os.getenv("STT_SMART_FORMAT", "true")),
        "vad_events": _bool_query(os.getenv("STT_VAD_EVENTS", "true")),
    }
    resolved_language = (language or os.getenv("STT_LANGUAGE") or "").strip()
    if resolved_language:
        query["language"] = resolved_language
    utterance_end_ms = (os.getenv("STT_UTTERANCE_END_MS") or "").strip()
    if utterance_end_ms:
        query["utterance_end_ms"] = utterance_end_ms
    return query


def _url_with_query(endpoint: str, query: dict[str, str]) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "wss":
        raise ProviderError(DeepgramStreamingSTTProvider.provider_name, "stt_streaming_endpoint_must_be_wss", "Streaming STT endpoint must use wss://")
    existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    merged = existing + [(key, value) for key, value in query.items() if value != ""]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(merged)))


def _bool_query(value: str) -> str:
    return "true" if str(value or "").strip().lower() in {"1", "true", "yes", "on"} else "false"


def _confidence_to_percent(value: Any) -> int | None:
    if isinstance(value, int | float):
        if 0 <= float(value) <= 1:
            return int(round(float(value) * 100))
        return int(round(float(value)))
    return None


def websocket_connect(*args, **kwargs):
    from websockets.sync.client import connect

    return connect(*args, **kwargs)
