from __future__ import annotations

import os

import httpx

from .base import ProviderError, STTProvider, STTResult
from .http_utils import classify_http_error, endpoint_required, read_secret_file, retry_call


class ExternalSTTProvider(STTProvider):
    provider_name = "external"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint
        self.token_file = token_file

    def transcribe(self, audio: bytes, *, language: str | None = None) -> STTResult:
        if not audio:
            raise ProviderError(self.provider_name, "stt_audio_required", "STT requires audio")
        endpoint = endpoint_required(self.endpoint, provider=self.provider_name)
        token = read_secret_file(self.token_file, provider=self.provider_name)
        timeout = float(os.getenv("STT_TIMEOUT_SECONDS", "12"))
        retries = int(os.getenv("STT_RETRIES", "1"))

        def request() -> STTResult:
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {token}"},
                        files={"audio": ("utterance.wav", audio, "audio/wav")},
                        data={"language": language or ""},
                    )
                    response.raise_for_status()
                    payload = response.json()
            except Exception as exc:
                raise classify_http_error(self.provider_name, exc) from exc
            text = str(payload.get("text") or payload.get("transcript") or "").strip()
            if not text:
                raise ProviderError(self.provider_name, "stt_empty_transcript", "STT returned no transcript")
            confidence = payload.get("confidence")
            return STTResult(
                text=text,
                language=str(payload.get("language") or language or "en"),
                confidence=int(confidence) if isinstance(confidence, int | float) else None,
                provider_name=self.provider_name,
            )

        return retry_call(request, provider=self.provider_name, retries=retries)
