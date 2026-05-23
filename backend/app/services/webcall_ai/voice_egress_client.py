from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import WebCallAISettings, get_webcall_ai_settings


@dataclass(frozen=True)
class WebCallVoiceEgressResult:
    sent: bool
    provider: str
    status: str
    audio_reference: str | None
    error_code: str | None = None


class VoiceEgressPublisher(Protocol):
    def send(self, *, audio_reference: str, participant_identity: str | None = None) -> bool:
        ...


class WebCallVoiceEgressClient(Protocol):
    def send_audio_reference(
        self,
        *,
        audio_reference: str,
        participant_identity: str | None = None,
    ) -> WebCallVoiceEgressResult:
        ...


class FakeAudioReferenceEgressClient:
    provider = "fake_audio_reference"

    def send_audio_reference(
        self,
        *,
        audio_reference: str,
        participant_identity: str | None = None,
    ) -> WebCallVoiceEgressResult:
        return WebCallVoiceEgressResult(
            sent=bool(audio_reference),
            provider=self.provider,
            status="sent_audio_reference" if audio_reference else "failed",
            audio_reference=audio_reference or None,
            error_code=None if audio_reference else "audio_reference_required",
        )


class LiveKitAudioPublishStubClient:
    provider = "livekit_audio_publish_stub"

    def __init__(self, publisher: VoiceEgressPublisher | None = None) -> None:
        self.publisher = publisher

    def send_audio_reference(
        self,
        *,
        audio_reference: str,
        participant_identity: str | None = None,
    ) -> WebCallVoiceEgressResult:
        if not audio_reference:
            return WebCallVoiceEgressResult(
                sent=False,
                provider=self.provider,
                status="failed",
                audio_reference=None,
                error_code="audio_reference_required",
            )
        if self.publisher is None:
            return WebCallVoiceEgressResult(
                sent=False,
                provider=self.provider,
                status="unavailable",
                audio_reference=audio_reference,
                error_code="livekit_audio_publish_unavailable",
            )
        try:
            sent = bool(self.publisher.send(audio_reference=audio_reference, participant_identity=participant_identity))
        except Exception:
            return WebCallVoiceEgressResult(
                sent=False,
                provider=self.provider,
                status="failed",
                audio_reference=audio_reference,
                error_code="livekit_audio_publish_failed",
            )
        return WebCallVoiceEgressResult(
            sent=sent,
            provider=self.provider,
            status="sent_audio_reference" if sent else "failed",
            audio_reference=audio_reference,
            error_code=None if sent else "livekit_audio_publish_failed",
        )


def get_webcall_voice_egress_client(settings: WebCallAISettings | None = None) -> WebCallVoiceEgressClient:
    resolved = settings or get_webcall_ai_settings()
    if resolved.voice_egress_mode == "fake_audio_reference":
        return FakeAudioReferenceEgressClient()
    if resolved.voice_egress_mode == "livekit_audio_publish_stub":
        return LiveKitAudioPublishStubClient()
    raise RuntimeError("WEBCALL_AI_VOICE_EGRESS_MODE must be fake_audio_reference or livekit_audio_publish_stub")
