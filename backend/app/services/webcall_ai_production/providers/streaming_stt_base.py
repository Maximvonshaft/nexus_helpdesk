from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from ..audio.livekit_io import PCMFrame


@dataclass(frozen=True)
class STTEvent:
    type: str
    text: str | None = None
    language: str | None = None
    confidence: int | None = None
    provider: str | None = None
    provider_session_id: str | None = None
    is_final: bool = False
    speech_final: bool = False
    raw_type: str | None = None


class StreamingSTTSession(Protocol):
    provider_name: str

    def start(self, *, language: str | None = None, sample_rate: int, channels: int) -> None: ...
    def send_pcm_frame(self, frame: PCMFrame) -> None: ...
    def poll_events(self) -> list[STTEvent]: ...
    def finalize(self) -> list[STTEvent]: ...
    def close(self) -> None: ...


def final_transcript_from_events(events: Iterable[STTEvent]) -> tuple[str, str | None, int | None]:
    final_events = [event for event in events if event.type == "final" and event.text]
    if final_events:
        text = " ".join(str(event.text or "").strip() for event in final_events if str(event.text or "").strip())
        last = final_events[-1]
        return " ".join(text.split()), last.language, last.confidence
    partial_events = [event for event in events if event.type == "partial" and event.text]
    if partial_events:
        last = partial_events[-1]
        return " ".join(str(last.text or "").split()), last.language, last.confidence
    return "", None, None
