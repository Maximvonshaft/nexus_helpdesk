from __future__ import annotations

from dataclasses import dataclass

from ..livekit_service import issue_join_token


@dataclass(frozen=True)
class LiveKitMediaTurn:
    customer_audio: bytes
    language: str | None = None


class LiveKitAgentIO:
    """Boundary for the real LiveKit media loop.

    The production worker may claim a session only after config is ready. This
    class then owns AI participant token issuance, room join, visitor audio
    collection, and AI audio publication. The concrete RTC implementation is
    intentionally isolated here so provider failures do not leak secrets or raw
    audio into the business orchestration layer.
    """

    def __init__(self, *, room_name: str, participant_identity: str, ttl_seconds: int) -> None:
        self.room_name = room_name
        self.participant_identity = participant_identity
        self.ttl_seconds = ttl_seconds
        self.token = issue_join_token(
            room_name=room_name,
            participant_identity=participant_identity,
            ttl_seconds=ttl_seconds,
        )

    def collect_next_customer_utterance(self) -> LiveKitMediaTurn:
        raise RuntimeError("LiveKit RTC media loop is not available in this build")

    def publish_ai_audio(self, audio_bytes: bytes, *, mime_type: str) -> None:
        if not audio_bytes:
            raise RuntimeError("AI audio publication requires non-empty audio bytes")
        raise RuntimeError("LiveKit RTC audio publication is not available in this build")

    def close(self) -> None:
        return None
