from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from .base import ProviderError, TTSProvider, TTSResult
from .http_utils import classify_http_error, read_secret_file
from .streaming_tts_base import TTSChunk

_DEFAULT_CARTESIA_SSE_URL = "https://api.cartesia.ai/tts/sse"
_DEFAULT_CARTESIA_VERSION = "2026-03-01"
_DEFAULT_MODEL = "sonic-3.5"
_RAW_PCM_MIME = "audio/pcm"


class CartesiaStreamingTTSProvider(TTSProvider):
    provider_name = "cartesia_streaming"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint or os.getenv("TTS_ENDPOINT") or _DEFAULT_CARTESIA_SSE_URL
        self.token_file = token_file or os.getenv("TTS_API_KEY_FILE")

    def synthesize(self, text: str, *, language: str | None = None) -> TTSResult:
        chunks = tuple(self.synthesize_stream(text, language=language))
        audio = b"".join(chunk.audio_bytes for chunk in chunks if chunk.audio_bytes)
        if not audio:
            raise ProviderError(self.provider_name, "tts_empty_audio", "TTS returned no audio")
        first = chunks[0]
        return TTSResult(
            audio_bytes=audio,
            mime_type=first.mime_type,
            text=text,
            provider_name=self.provider_name,
            audio_chunks=chunks,
        )

    def synthesize_lazy(self, text: str, *, language: str | None = None) -> TTSResult:
        return TTSResult(
            audio_bytes=b"",
            mime_type=_RAW_PCM_MIME,
            text=text,
            provider_name=self.provider_name,
            audio_stream=self.synthesize_stream(text, language=language),
        )

    def synthesize_stream(self, text: str, *, language: str | None = None) -> Iterable[TTSChunk]:
        if not (text or "").strip():
            raise ProviderError(self.provider_name, "tts_text_required", "TTS requires response text")
        endpoint = _endpoint_required(self.endpoint, provider=self.provider_name)
        token = read_secret_file(self.token_file, provider=self.provider_name)
        sample_rate = _int_env("TTS_SAMPLE_RATE", _int_env("WEBCALL_AI_TTS_SAMPLE_RATE", 24000, minimum=8000, maximum=48000), minimum=8000, maximum=48000)
        channels = _int_env("TTS_CHANNELS", _int_env("WEBCALL_AI_TTS_CHANNELS", 1, minimum=1, maximum=2), minimum=1, maximum=2)
        timeout = float(os.getenv("TTS_TIMEOUT_SECONDS", "20"))
        payload = _cartesia_payload(text=text, language=language, sample_rate=sample_rate)
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST",
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Cartesia-Version": os.getenv("CARTESIA_VERSION", _DEFAULT_CARTESIA_VERSION),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    yielded = False
                    for line in response.iter_lines():
                        event = parse_cartesia_sse_line(line, sample_rate=sample_rate, channels=channels)
                        if event is None:
                            continue
                        if event.error_code:
                            raise ProviderError(self.provider_name, event.error_code, event.error_message or "Cartesia SSE error")
                        if event.done:
                            break
                        if event.chunk is not None:
                            yielded = True
                            yield event.chunk
                    if not yielded:
                        raise ProviderError(self.provider_name, "tts_empty_audio", "TTS returned no audio chunks")
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_http_error(self.provider_name, exc) from exc


@dataclass(frozen=True)
class CartesiaSSEEvent:
    chunk: TTSChunk | None = None
    done: bool = False
    error_code: str | None = None
    error_message: str | None = None


def parse_cartesia_sse_line(line: str | bytes, *, sample_rate: int, channels: int) -> CartesiaSSEEvent | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="ignore")
    cleaned = line.strip()
    if not cleaned or cleaned.startswith(":"):
        return None
    if cleaned.startswith("data:"):
        cleaned = cleaned.split(":", 1)[1].strip()
    else:
        return None
    if not cleaned or cleaned == "[DONE]":
        return CartesiaSSEEvent(done=True)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return CartesiaSSEEvent(error_code="tts_sse_parse_error", error_message="Invalid Cartesia SSE JSON")
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get("type") or "")
    if payload.get("done") is True or event_type == "done":
        return CartesiaSSEEvent(done=True)
    if event_type == "error":
        return CartesiaSSEEvent(
            error_code=str(payload.get("error_code") or payload.get("code") or "tts_provider_error")[:120],
            error_message=str(payload.get("message") or payload.get("error") or "Cartesia SSE error")[:500],
        )
    if event_type != "chunk":
        return None
    data = str(payload.get("data") or "")
    if not data:
        return None
    try:
        audio = base64.b64decode(data)
    except Exception:
        return CartesiaSSEEvent(error_code="tts_chunk_decode_error", error_message="Invalid Cartesia audio chunk")
    if not audio:
        return None
    return CartesiaSSEEvent(
        chunk=TTSChunk(
            audio_bytes=audio,
            mime_type=_RAW_PCM_MIME,
            sample_rate=sample_rate,
            channels=channels,
            provider_latency_ms=_number_to_int(payload.get("step_time")),
            provider_name=CartesiaStreamingTTSProvider.provider_name,
            context_id=str(payload.get("context_id") or "") or None,
        )
    )


def _cartesia_payload(*, text: str, language: str | None, sample_rate: int) -> dict[str, Any]:
    voice_id = (os.getenv("TTS_VOICE_ID") or "").strip()
    if not voice_id:
        raise ProviderError(CartesiaStreamingTTSProvider.provider_name, "tts_voice_id_required", "TTS_VOICE_ID is required")
    payload: dict[str, Any] = {
        "model_id": os.getenv("TTS_MODEL", _DEFAULT_MODEL),
        "transcript": text,
        "voice": {"id": voice_id},
        "output_format": {
            "container": "RAW",
            "encoding": os.getenv("TTS_ENCODING", "pcm_s16le"),
            "sample_rate": sample_rate,
        },
        "context_id": f"webcall-ai-{uuid.uuid4().hex}",
    }
    resolved_language = (language or os.getenv("TTS_LANGUAGE") or "").strip()
    if resolved_language:
        payload["language"] = resolved_language
    speed = (os.getenv("TTS_SPEED") or "").strip()
    if speed:
        payload["speed"] = speed
    return payload


def _endpoint_required(endpoint: str | None, *, provider: str) -> str:
    value = (endpoint or "").strip()
    if not value:
        raise ProviderError(provider, "endpoint_required", "provider endpoint is required")
    if not value.startswith("https://"):
        raise ProviderError(provider, "endpoint_invalid", "provider endpoint must be https")
    return value


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _number_to_int(value: Any) -> int | None:
    if isinstance(value, int | float):
        return int(value)
    return None
