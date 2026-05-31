from __future__ import annotations

import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterable

from .base import ProviderError, TTSProvider, TTSResult
from .cancel_token import CancelToken
from .http_utils import read_secret_file
from .streaming_tts_base import TTSChunk

_DEFAULT_DEEPGRAM_TTS_URL = "wss://api.deepgram.com/v1/speak"
_DEFAULT_MODEL = "aura-2-thalia-en"
_DEFAULT_ENCODING = "linear16"
_DEFAULT_SAMPLE_RATE = 48000
_RAW_PCM_MIME = "audio/pcm"


class DeepgramStreamingTTSProvider(TTSProvider):
    provider_name = "deepgram_streaming"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint or os.getenv("TTS_ENDPOINT") or _DEFAULT_DEEPGRAM_TTS_URL
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

    def synthesize_lazy(self, text: str, *, language: str | None = None, cancel_token: CancelToken | None = None) -> TTSResult:
        token = cancel_token or CancelToken()
        return TTSResult(
            audio_bytes=b"",
            mime_type=_RAW_PCM_MIME,
            text=text,
            provider_name=self.provider_name,
            audio_stream=self.synthesize_stream(text, language=language, cancel_token=token),
            cancel_token=token,
        )

    def synthesize_stream(self, text: str, *, language: str | None = None, cancel_token: CancelToken | None = None) -> Iterable[TTSChunk]:
        session = DeepgramStreamingTTSSession(endpoint=self.endpoint, token_file=self.token_file)
        token = cancel_token or CancelToken()
        try:
            session.start()
            yield from session.synthesize(text, cancel_token=token)
        finally:
            session.close(cancelled=token.cancelled)


@dataclass
class DeepgramStreamingTTSSession:
    endpoint: str | None = None
    token_file: str | None = None
    provider_name: str = DeepgramStreamingTTSProvider.provider_name
    _ws: Any = field(default=None, init=False, repr=False)
    _started_at: float | None = field(default=None, init=False)

    def start(self) -> None:
        if self._ws is not None:
            return
        endpoint = (self.endpoint or _DEFAULT_DEEPGRAM_TTS_URL).strip()
        if not endpoint:
            raise ProviderError(self.provider_name, "endpoint_required", "provider endpoint is required")
        token = read_secret_file(self.token_file, provider=self.provider_name)
        self._started_at = time.monotonic()
        self._ws = websocket_connect(
            _url_with_query(endpoint, _deepgram_tts_query()),
            additional_headers={"Authorization": f"Token {token}"},
            open_timeout=float(os.getenv("TTS_CONNECT_TIMEOUT_SECONDS", "5")),
            close_timeout=float(os.getenv("TTS_CLOSE_TIMEOUT_SECONDS", "3")),
        )

    def synthesize(self, text: str, *, cancel_token: CancelToken) -> Iterable[TTSChunk]:
        if self._ws is None:
            raise ProviderError(self.provider_name, "tts_stream_not_started", "Streaming TTS session has not started")
        if not (text or "").strip():
            raise ProviderError(self.provider_name, "tts_text_required", "TTS requires response text")
        self._send_json({"type": "Speak", "text": text})
        self._send_json({"type": "Flush"})
        sample_rate = _int_env("TTS_SAMPLE_RATE", _int_env("WEBCALL_AI_TTS_SAMPLE_RATE", _DEFAULT_SAMPLE_RATE, minimum=8000, maximum=48000), minimum=8000, maximum=48000)
        channels = _int_env("TTS_CHANNELS", _int_env("WEBCALL_AI_TTS_CHANNELS", 1, minimum=1, maximum=2), minimum=1, maximum=2)
        deadline = time.monotonic() + float(os.getenv("TTS_FINAL_TIMEOUT_SECONDS", "20"))
        first_audio = True
        while time.monotonic() < deadline:
            if cancel_token.cancelled:
                self._send_json({"type": "Clear"})
                break
            try:
                raw = self._ws.recv(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except TimeoutError:
                continue
            except Exception as exc:
                raise ProviderError(self.provider_name, "tts_stream_recv_failed", "Deepgram TTS stream receive failed") from exc
            if raw is None:
                break
            if isinstance(raw, bytes):
                if not raw:
                    continue
                latency_ms = int((time.monotonic() - (self._started_at or time.monotonic())) * 1000) if first_audio else None
                first_audio = False
                yield TTSChunk(
                    audio_bytes=raw,
                    mime_type=_RAW_PCM_MIME,
                    sample_rate=sample_rate,
                    channels=channels,
                    provider_latency_ms=latency_ms,
                    provider_name=self.provider_name,
                )
                continue
            event = parse_deepgram_tts_event(raw)
            if event is None:
                continue
            if event.error_code:
                raise ProviderError(self.provider_name, event.error_code, event.error_message or "Deepgram TTS error")
            if event.done:
                break

    def close(self, *, cancelled: bool = False) -> None:
        if self._ws is None:
            return
        try:
            if cancelled:
                self._send_json({"type": "Clear"})
            self._send_json({"type": "Close"})
        except Exception:
            pass
        try:
            self._ws.close()
        finally:
            self._ws = None

    def _send_json(self, payload: dict[str, Any]) -> None:
        if self._ws is not None:
            self._ws.send(json.dumps(payload))


@dataclass(frozen=True)
class DeepgramTTSEvent:
    done: bool = False
    error_code: str | None = None
    error_message: str | None = None


def parse_deepgram_tts_event(raw: str | bytes) -> DeepgramTTSEvent | None:
    if isinstance(raw, bytes):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get("type") or "")
    if event_type in {"Flushed", "Close", "Closed"}:
        return DeepgramTTSEvent(done=True)
    if event_type == "Error":
        return DeepgramTTSEvent(
            error_code=str(payload.get("code") or "tts_provider_error")[:120],
            error_message=str(payload.get("description") or payload.get("message") or "Deepgram TTS error")[:500],
        )
    return None


def _deepgram_tts_query() -> dict[str, str]:
    query = {
        "model": os.getenv("TTS_MODEL", _DEFAULT_MODEL),
        "encoding": os.getenv("TTS_ENCODING", _DEFAULT_ENCODING),
        "sample_rate": str(_int_env("TTS_SAMPLE_RATE", _int_env("WEBCALL_AI_TTS_SAMPLE_RATE", _DEFAULT_SAMPLE_RATE, minimum=8000, maximum=48000), minimum=8000, maximum=48000)),
    }
    speed = (os.getenv("TTS_SPEED") or "").strip()
    if speed:
        query["speed"] = speed
    return query


def _url_with_query(endpoint: str, query: dict[str, str]) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "wss":
        raise ProviderError(DeepgramStreamingTTSProvider.provider_name, "tts_streaming_endpoint_must_be_wss", "Streaming TTS endpoint must use wss://")
    existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    merged = existing + [(key, value) for key, value in query.items() if value != ""]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(merged)))


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def websocket_connect(*args, **kwargs):
    from websockets.sync.client import connect

    return connect(*args, **kwargs)
