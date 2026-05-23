from __future__ import annotations

from .media_schemas import WebCallSTTInput, WebCallSTTResult, WebCallTTSInput, WebCallTTSResult


class DisabledSTTProvider:
    name = "disabled"

    def transcribe(self, input: WebCallSTTInput) -> WebCallSTTResult:
        return WebCallSTTResult(
            text_redacted=None,
            language=input.locale or "en",
            confidence=None,
            is_final=False,
            provider=self.name,
            event_count=0,
            status="disabled",
            error_code="stt_provider_disabled",
        )


class DisabledTTSProvider:
    name = "disabled"

    def synthesize(self, input: WebCallTTSInput) -> WebCallTTSResult:
        return WebCallTTSResult(
            provider=self.name,
            voice=input.voice,
            language=input.language,
            text_redacted=input.text_redacted,
            synthesis_status="disabled",
            audio_reference=None,
            event_count=0,
            error_code="tts_provider_disabled",
        )


class ContractStubSTTProvider:
    name = "contract_stub"

    def transcribe(self, input: WebCallSTTInput) -> WebCallSTTResult:
        return WebCallSTTResult(
            text_redacted=None,
            language=input.locale or "en",
            confidence=None,
            is_final=False,
            provider=self.name,
            event_count=0,
            status="unavailable",
            error_code="stt_contract_stub_not_implemented",
        )


class ContractStubTTSProvider:
    name = "contract_stub"

    def synthesize(self, input: WebCallTTSInput) -> WebCallTTSResult:
        return WebCallTTSResult(
            provider=self.name,
            voice=input.voice,
            language=input.language,
            text_redacted=input.text_redacted,
            synthesis_status="unavailable",
            audio_reference=None,
            event_count=0,
            error_code="tts_contract_stub_not_implemented",
        )
