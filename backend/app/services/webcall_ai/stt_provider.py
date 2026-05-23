from __future__ import annotations

from typing import Protocol

from .media_schemas import MockSTTInput, MockSTTResult


class STTProvider(Protocol):
    name: str

    def transcribe(self, input: MockSTTInput) -> MockSTTResult:
        ...
