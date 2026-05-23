from __future__ import annotations

from .media_schemas import MockSTTInput, MockSTTResult, MockTTSInput, MockTTSResult

MOCK_CUSTOMER_TEXT = "I want to check my parcel status."
MOCK_TTS_AUDIO_REFERENCE = "mock://tts/webcall-ai-support-greeting"
MOCK_TTS_SYNTHESIS_STATUS = "mock_synthesized"


class MockSTTProvider:
    name = "mock"

    def transcribe(self, input: MockSTTInput) -> MockSTTResult:
        return MockSTTResult(
            text_redacted=MOCK_CUSTOMER_TEXT,
            language=input.locale or "en",
            confidence=100,
            is_final=True,
            provider=self.name,
            event_count=1,
        )


class MockTTSProvider:
    name = "mock"

    def synthesize(self, input: MockTTSInput) -> MockTTSResult:
        return MockTTSResult(
            provider=self.name,
            voice=input.voice,
            language=input.language,
            text_redacted=input.text_redacted,
            synthesis_status=MOCK_TTS_SYNTHESIS_STATUS,
            audio_reference=MOCK_TTS_AUDIO_REFERENCE,
            event_count=1,
        )
