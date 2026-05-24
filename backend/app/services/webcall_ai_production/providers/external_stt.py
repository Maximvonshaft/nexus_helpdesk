from __future__ import annotations

import os

import httpx

from ..audio.livekit_io import pcm16_to_wav
from .base import ProviderError, STTProvider, STTResult
from .http_utils import classify_http_error, endpoint_required, read_secret_file, retry_call


class ExternalSTTProvider(STTProvider):
    provider_name = "external"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint
        self.token_file = token_file

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        mime_type: str | None = None,
    ) -> STTResult:
        if not audio:
            raise ProviderError(self.provider_name, "stt_audio_required", "STT requires audio")
        endpoint = endpoint_required(self.endpoint, provider=self.provider_name)
        token = read_secret_file(self.token_file, provider=self.provider_name)
        timeout = float(os.getenv("STT_TIMEOUT_SECONDS", "12"))
        retries = int(os.getenv("STT_RETRIES", "1"))

        upload_bytes, upload_name, upload_mime = prepare_stt_audio_upload(
            audio,
            sample_rate=sample_rate,
            channels=channels,
            mime_type=mime_type,
        )

        def request() -> STTResult:
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {token}"},
                        files={"audio": (upload_name, upload_bytes, upload_mime)},
                        data={"language": language or "", "sample_rate": str(sample_rate or ""), "channels": str(channels or "")},
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


def prepare_stt_audio_upload(
    audio: bytes,
    *,
    sample_rate: int | None,
    channels: int | None,
    mime_type: str | None,
) -> tuple[bytes, str, str]:
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized in {"audio/wav", "audio/x-wav", "audio/wave"} or audio.startswith(b"RIFF"):
        return audio, "utterance.wav", "audio/wav"
    if normalized in {"audio/pcm", "audio/l16", "application/octet-stream", ""}:
        if not sample_rate or not channels:
            raise ProviderError("external", "stt_pcm_metadata_required", "raw PCM requires sample_rate and channels")
        return pcm16_to_wav(audio, sample_rate=sample_rate, channels=channels), "utterance.wav", "audio/wav"
    return audio, "utterance.bin", normalized
