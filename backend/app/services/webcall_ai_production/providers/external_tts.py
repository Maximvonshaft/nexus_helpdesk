from __future__ import annotations

from .base import TTSProvider, TTSResult


class ExternalTTSProvider(TTSProvider):
    provider_name = "external"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint
        self.token_file = token_file

    def synthesize(self, text: str, *, language: str | None = None) -> TTSResult:
        raise RuntimeError("external TTS adapter must be wired to the approved provider before production use")
