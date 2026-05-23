from __future__ import annotations

from .base import STTProvider, STTResult


class ExternalSTTProvider(STTProvider):
    provider_name = "external"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint
        self.token_file = token_file

    def transcribe(self, audio: bytes, *, language: str | None = None) -> STTResult:
        raise RuntimeError("external STT streaming adapter must be wired to the approved provider before production use")
