from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True)
class TTSChunk:
    audio_bytes: bytes
    mime_type: str
    sample_rate: int
    channels: int
    is_final: bool = False
    provider_latency_ms: int | None = None
    provider_name: str | None = None
    context_id: str | None = None


class StreamingTTSProvider(Protocol):
    provider_name: str

    def synthesize_stream(self, text: str, *, language: str | None = None) -> Iterable[TTSChunk]: ...
