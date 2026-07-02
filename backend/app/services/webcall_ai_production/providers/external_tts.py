from __future__ import annotations

import base64
import os
from typing import Any
from urllib.parse import urljoin

import httpx

from .base import ProviderError, TTSProvider, TTSResult
from .http_utils import classify_http_error, endpoint_required, read_secret_file, retry_call


class ExternalTTSProvider(TTSProvider):
    provider_name = "external"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint
        self.token_file = token_file

    def synthesize(self, text: str, *, language: str | None = None) -> TTSResult:
        if not (text or "").strip():
            raise ProviderError(self.provider_name, "tts_text_required", "TTS requires response text")
        endpoint = endpoint_required(self.endpoint, provider=self.provider_name)
        token = read_secret_file(self.token_file, provider=self.provider_name)
        timeout = float(os.getenv("TTS_TIMEOUT_SECONDS", "15"))
        retries = int(os.getenv("TTS_RETRIES", "1"))
        voice = os.getenv("TTS_VOICE", "support")

        def request() -> TTSResult:
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {token}"},
                        json={"text": text, "language": language or "en", "voice": voice, "format": "wav"},
                    )
                    response.raise_for_status()
                    audio, content_type = _extract_tts_audio(
                        client,
                        endpoint=endpoint,
                        token=token,
                        response=response,
                        timeout=timeout,
                    )
            except Exception as exc:
                raise classify_http_error(self.provider_name, exc) from exc
            if not audio:
                raise ProviderError(self.provider_name, "tts_empty_audio", "TTS returned no audio")
            return TTSResult(audio_bytes=audio, mime_type=content_type, text=text, provider_name=self.provider_name)

        return retry_call(request, provider=self.provider_name, retries=retries)


def _extract_tts_audio(
    client: httpx.Client,
    *,
    endpoint: str,
    token: str,
    response: httpx.Response,
    timeout: float,
) -> tuple[bytes, str]:
    content_type = response.headers.get("content-type", "audio/wav").split(";")[0].strip().lower()
    if content_type.startswith("audio/") or response.content.startswith(b"RIFF"):
        return response.content, content_type or "audio/wav"

    payload = _json_payload(response)
    encoded = _first_string(payload, "audio_base64", "audio", "wav_base64", "content_base64")
    if encoded:
        try:
            return base64.b64decode(encoded), str(payload.get("mime_type") or payload.get("content_type") or "audio/wav")
        except Exception as exc:
            raise ProviderError("external", "tts_audio_base64_invalid", "TTS returned invalid base64 audio") from exc

    audio_url = _first_string(payload, "file_url", "audio_url", "url", "download_url")
    if audio_url:
        resolved = urljoin(endpoint, audio_url)
        audio_response = client.get(resolved, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        audio_response.raise_for_status()
        fetched_type = audio_response.headers.get("content-type", "audio/wav").split(";")[0].strip().lower()
        return audio_response.content, fetched_type or "audio/wav"

    raise ProviderError("external", "tts_audio_reference_missing", "TTS returned JSON without audio payload")


def _json_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderError("external", "tts_json_invalid", "TTS returned non-audio response") from exc
    if not isinstance(payload, dict):
        raise ProviderError("external", "tts_json_not_object", "TTS returned invalid JSON response")
    return payload


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
