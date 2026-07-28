from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class VoiceSessionTiming(Protocol):
    started_at: datetime | None
    accepted_at: datetime | None
    active_at: datetime | None
    ended_at: datetime | None


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def elapsed_seconds(
    started_at: datetime | None,
    ended_at: datetime | None,
) -> int | None:
    """Derive a non-negative duration from immutable lifecycle timestamps."""

    start = _aware_utc(started_at)
    end = _aware_utc(ended_at)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


def voice_talk_duration_seconds(session: VoiceSessionTiming) -> int | None:
    """Return canonical talk duration for human and AI voice sessions.

    Accepted is authoritative when present, then active, then started. This keeps
    historical sessions measurable without inventing a persisted duration field.
    """

    return elapsed_seconds(
        session.accepted_at or session.active_at or session.started_at,
        session.ended_at,
    )


__all__ = ["elapsed_seconds", "voice_talk_duration_seconds"]
