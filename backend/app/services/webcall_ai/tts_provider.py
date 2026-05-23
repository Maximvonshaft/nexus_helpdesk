from __future__ import annotations

from typing import Protocol

from .media_schemas import MockTTSInput, MockTTSResult


class TTSProvider(Protocol):
    name: str

    def synthesize(self, input: MockTTSInput) -> MockTTSResult:
        ...
