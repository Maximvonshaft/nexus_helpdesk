from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any


AUDIO_PRESENT = "audio_present"
DEEPGRAM_EMPTY_TRANSCRIPT = "deepgram_empty_transcript"
NO_REMOTE_AUDIO_TRACK = "no_remote_audio_track"
AUDIO_TRACK_MUTED = "audio_track_muted"
NO_PCM_FRAMES = "no_pcm_frames"
PCM_TOO_SHORT = "pcm_too_short"
PCM_SILENT = "pcm_silent"


@dataclass(frozen=True)
class PCMAudioStats:
    frame_count: int
    audio_ms: int
    pcm_bytes: int
    sample_rate: int
    channels: int
    rms_min: int
    rms_avg: int
    rms_max: int
    classification: str

    def as_payload(self) -> dict[str, int | str]:
        return {
            "frame_count": self.frame_count,
            "audio_ms": self.audio_ms,
            "pcm_bytes": self.pcm_bytes,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "rms_min": self.rms_min,
            "rms_avg": self.rms_avg,
            "rms_max": self.rms_max,
            "audio_input_classification": self.classification,
        }


def analyze_pcm16_audio(
    pcm: bytes,
    *,
    sample_rate: int | None,
    channels: int | None,
    frame_count: int | None = None,
    remote_track_seen: bool = True,
    audio_track_muted: bool = False,
) -> PCMAudioStats:
    resolved_sample_rate = int(sample_rate or 0)
    resolved_channels = int(channels or 0)
    pcm_bytes = len(pcm or b"")
    resolved_frame_count = int(frame_count if frame_count is not None else _estimated_frame_count(pcm_bytes, sample_rate=resolved_sample_rate, channels=resolved_channels))
    audio_ms = _audio_duration_ms(pcm_bytes, sample_rate=resolved_sample_rate, channels=resolved_channels)
    rms_values = _frame_rms_values(pcm or b"", sample_rate=resolved_sample_rate, channels=resolved_channels)
    rms_min = min(rms_values) if rms_values else 0
    rms_max = max(rms_values) if rms_values else 0
    rms_avg = int(sum(rms_values) / len(rms_values)) if rms_values else 0
    classification = classify_pcm_audio(
        frame_count=resolved_frame_count,
        audio_ms=audio_ms,
        pcm_bytes=pcm_bytes,
        rms_max=rms_max,
        remote_track_seen=remote_track_seen,
        audio_track_muted=audio_track_muted,
    )
    return PCMAudioStats(
        frame_count=resolved_frame_count,
        audio_ms=audio_ms,
        pcm_bytes=pcm_bytes,
        sample_rate=resolved_sample_rate,
        channels=resolved_channels,
        rms_min=int(rms_min),
        rms_avg=int(rms_avg),
        rms_max=int(rms_max),
        classification=classification,
    )


def classify_empty_transcript(stats: PCMAudioStats | dict[str, Any] | None) -> str:
    if stats is None:
        return NO_PCM_FRAMES
    classification = stats.classification if isinstance(stats, PCMAudioStats) else str(stats.get("audio_input_classification") or "")
    return DEEPGRAM_EMPTY_TRANSCRIPT if classification == AUDIO_PRESENT else (classification or NO_PCM_FRAMES)


def classify_pcm_audio(
    *,
    frame_count: int,
    audio_ms: int,
    pcm_bytes: int,
    rms_max: int,
    remote_track_seen: bool,
    audio_track_muted: bool,
) -> str:
    if not remote_track_seen:
        return NO_REMOTE_AUDIO_TRACK
    if audio_track_muted:
        return AUDIO_TRACK_MUTED
    if frame_count <= 0 or pcm_bytes <= 0:
        return NO_PCM_FRAMES
    if audio_ms < _min_audio_ms():
        return PCM_TOO_SHORT
    if rms_max <= _silence_rms_threshold():
        return PCM_SILENT
    return AUDIO_PRESENT


def _audio_duration_ms(pcm_bytes: int, *, sample_rate: int, channels: int) -> int:
    if sample_rate <= 0 or channels <= 0 or pcm_bytes <= 0:
        return 0
    samples_per_channel = pcm_bytes / 2 / channels
    return int((samples_per_channel / sample_rate) * 1000)


def _estimated_frame_count(pcm_bytes: int, *, sample_rate: int, channels: int) -> int:
    if pcm_bytes <= 0 or sample_rate <= 0 or channels <= 0:
        return 0
    frame_bytes = max(1, int(sample_rate * 0.02) * channels * 2)
    return math.ceil(pcm_bytes / frame_bytes)


def _frame_rms_values(pcm: bytes, *, sample_rate: int, channels: int) -> list[int]:
    if not pcm:
        return []
    frame_bytes = max(2, int((sample_rate or 48000) * 0.02) * max(1, channels or 1) * 2)
    values: list[int] = []
    for offset in range(0, len(pcm), frame_bytes):
        frame = pcm[offset : offset + frame_bytes]
        rms = _rms_pcm16(frame)
        values.append(rms)
    return values


def _rms_pcm16(pcm: bytes) -> int:
    sample_count = max(0, len(pcm) // 2)
    if sample_count <= 0:
        return 0
    total = 0
    for offset in range(0, len(pcm) - 1, 2):
        sample = int.from_bytes(pcm[offset : offset + 2], byteorder="little", signed=True)
        total += sample * sample
    return int(math.sqrt(total / sample_count))


def _min_audio_ms() -> int:
    try:
        return max(0, min(int(os.getenv("WEBCALL_AI_STT_MIN_AUDIO_MS", "300")), 10000))
    except ValueError:
        return 300


def _silence_rms_threshold() -> int:
    try:
        return max(0, min(int(os.getenv("WEBCALL_AI_STT_SILENCE_RMS_THRESHOLD", "80")), 32000))
    except ValueError:
        return 80
