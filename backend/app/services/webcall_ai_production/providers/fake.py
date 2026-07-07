from __future__ import annotations

from .base import LLMProvider, LLMResult, STTProvider, STTResult, TTSProvider, TTSResult


class FakeSTTProvider(STTProvider):
    provider_name = "fake"

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        mime_type: str | None = None,
    ) -> STTResult:
        text = audio.decode("utf-8", errors="ignore").strip() if audio else ""
        return STTResult(text=text or "where is my package", language=language or "en", confidence=100, provider_name=self.provider_name)


class FakeLLMProvider(LLMProvider):
    provider_name = "fake"

    def respond(self, text: str, *, language: str | None = None) -> LLMResult:
        normalized = (text or "").lower()
        if any(ch.isdigit() for ch in normalized):
            return LLMResult(
                response_text="",
                intent="tracking_lookup",
                provider_name=self.provider_name,
            )
        return LLMResult(
            response_text="",
            intent="tracking_number_required",
            provider_name=self.provider_name,
        )


class FakeTTSProvider(TTSProvider):
    provider_name = "fake"

    def synthesize(self, text: str, *, language: str | None = None) -> TTSResult:
        return TTSResult(audio_bytes=text.encode("utf-8"), mime_type="text/plain; charset=utf-8", text=text, provider_name=self.provider_name)
