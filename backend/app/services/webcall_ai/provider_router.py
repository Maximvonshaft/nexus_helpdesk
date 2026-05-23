from __future__ import annotations

from .config import WebCallAISettings, get_webcall_ai_settings
from .contract_stub_provider import (
    ContractStubSTTProvider,
    ContractStubTTSProvider,
    DisabledSTTProvider,
    DisabledTTSProvider,
)
from .deepgram_stt_provider import DeepgramSTTProvider
from .mock_media_provider import MockSTTProvider, MockTTSProvider
from .stt_provider import STTProvider
from .tts_provider import TTSProvider


def get_stt_provider(settings: WebCallAISettings | None = None) -> STTProvider:
    resolved = settings or get_webcall_ai_settings()
    if resolved.stt_provider == "mock":
        return MockSTTProvider()
    if resolved.stt_provider == "disabled":
        return DisabledSTTProvider()
    if resolved.stt_provider == "contract_stub":
        return ContractStubSTTProvider()
    if resolved.stt_provider == "deepgram":
        return DeepgramSTTProvider(resolved)
    raise RuntimeError("WEBCALL_STT_PROVIDER is not allowed in PR-6")


def get_tts_provider(settings: WebCallAISettings | None = None) -> TTSProvider:
    resolved = settings or get_webcall_ai_settings()
    if resolved.tts_provider == "mock":
        return MockTTSProvider()
    if resolved.tts_provider == "disabled":
        return DisabledTTSProvider()
    if resolved.tts_provider == "contract_stub":
        return ContractStubTTSProvider()
    raise RuntimeError("WEBCALL_TTS_PROVIDER is not allowed in PR-5")
