from __future__ import annotations

from typing import Protocol

from .media_schemas import WebCallSTTInput, WebCallSTTResult


class STTProvider(Protocol):
    name: str

    def transcribe(self, input: WebCallSTTInput) -> WebCallSTTResult:
        ...
