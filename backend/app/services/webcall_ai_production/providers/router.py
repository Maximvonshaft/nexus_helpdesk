from __future__ import annotations

import os

from .base import LLMProvider, STTProvider, TTSProvider
from .cartesia_streaming_tts import CartesiaStreamingTTSProvider
from .deepgram_streaming_stt import DeepgramStreamingSTTProvider
from .deepgram_streaming_tts import DeepgramStreamingTTSProvider
from .external_llm import ExternalLLMProvider
from .external_stt import ExternalSTTProvider
from .external_tts import ExternalTTSProvider
from .fake import FakeLLMProvider, FakeSTTProvider, FakeTTSProvider
from .provider_runtime_llm import ProviderRuntimeLLMProvider


def get_stt_provider(name: str) -> STTProvider:
    if name == "fake":
        return FakeSTTProvider()
    if name == "external":
        return ExternalSTTProvider(endpoint=os.getenv("STT_ENDPOINT"), token_file=os.getenv("STT_API_KEY_FILE"))
    if name == "deepgram_streaming":
        return DeepgramStreamingSTTProvider(endpoint=os.getenv("STT_ENDPOINT"), token_file=os.getenv("STT_API_KEY_FILE"))
    raise RuntimeError(f"unsupported STT_PROVIDER={name}")


def get_llm_provider(name: str) -> LLMProvider:
    if name == "fake":
        return FakeLLMProvider()
    if name == "external":
        return ExternalLLMProvider(endpoint=os.getenv("LLM_ENDPOINT"), token_file=os.getenv("LLM_API_KEY_FILE"))
    if name == "provider_runtime":
        return ProviderRuntimeLLMProvider()
    raise RuntimeError(f"unsupported LLM_PROVIDER={name}")


def get_tts_provider(name: str) -> TTSProvider:
    if name == "fake":
        return FakeTTSProvider()
    if name == "external":
        return ExternalTTSProvider(endpoint=os.getenv("TTS_ENDPOINT"), token_file=os.getenv("TTS_API_KEY_FILE"))
    if name == "cartesia_streaming":
        return CartesiaStreamingTTSProvider(endpoint=os.getenv("TTS_ENDPOINT"), token_file=os.getenv("TTS_API_KEY_FILE"))
    if name == "deepgram_streaming":
        return DeepgramStreamingTTSProvider(endpoint=os.getenv("TTS_ENDPOINT"), token_file=os.getenv("TTS_API_KEY_FILE"))
    raise RuntimeError(f"unsupported TTS_PROVIDER={name}")
