from __future__ import annotations

import os

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
                    content_type = response.headers.get("content-type", "audio/wav").split(";")[0]
                    audio = response.content
            except Exception as exc:
                raise classify_http_error(self.provider_name, exc) from exc
            if not audio:
                raise ProviderError(self.provider_name, "tts_empty_audio", "TTS returned no audio")
            return TTSResult(audio_bytes=audio, mime_type=content_type, text=text, provider_name=self.provider_name)

        return retry_call(request, provider=self.provider_name, retries=retries)
