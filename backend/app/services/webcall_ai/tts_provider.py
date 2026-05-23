from __future__ import annotations

from typing import Protocol

from .media_schemas import WebCallTTSInput, WebCallTTSResult


class TTSProvider(Protocol):
    name: str

    def synthesize(self, input: WebCallTTSInput) -> WebCallTTSResult:
        ...
