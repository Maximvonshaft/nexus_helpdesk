from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.webcall_ai_production.audio.livekit_io import PCMFrame, SDKLiveKitRTCBackend  # noqa: E402


def _pcm_frame(amplitude: int, *, ms: int = 10, sample_rate: int = 48000) -> PCMFrame:
    samples = int(sample_rate * ms / 1000)
    pcm = int(amplitude).to_bytes(2, "little", signed=True) * samples
    return PCMFrame(
        data=pcm,
        sample_rate=sample_rate,
        channels=1,
        track_sid="TRK_audio_capture_test",
        participant_identity="visitor_capture_test",
        muted=False,
    )


def _repeat(amplitude: int, *, frames: int, ms: int = 10) -> list[PCMFrame]:
    return [_pcm_frame(amplitude, ms=ms) for _ in range(frames)]


async def _collect(frames: list[PCMFrame], *, timeout_seconds: float = 0.05, max_seconds: float = 8.0):
    backend = SDKLiveKitRTCBackend()
    backend._audio_queue = asyncio.Queue()
    backend._remote_audio_track_seen = True
    backend._remote_audio_track_muted = False
    backend._remote_track_sid = "TRK_audio_capture_test"
    backend._remote_participant_identity = "visitor_capture_test"
    for frame in frames:
        backend._audio_queue.put_nowait(frame)
    return await backend._collect_next_customer_utterance(timeout_seconds=timeout_seconds, max_seconds=max_seconds)


def test_1520ms_speech_window_does_not_finalize_before_minimum(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_MIN_UTTERANCE_AUDIO_MS", "4000")
    monkeypatch.setenv("WEBCALL_AI_MAX_UTTERANCE_AUDIO_MS", "8000")
    monkeypatch.setenv("WEBCALL_AI_SILENCE_END_MS", "500")
    frames = [
        *_repeat(2200, frames=100),
        *_repeat(0, frames=52),
        *_repeat(2200, frames=260),
        *_repeat(0, frames=50),
    ]

    turn = asyncio.run(_collect(frames))

    assert turn.audio_stats is not None
    assert turn.audio_stats["audio_ms"] >= 4000
    assert turn.audio_stats["frame_count"] > 152
    assert turn.audio_stats["audio_input_classification"] == "audio_present"
    assert turn.audio_stats["capture_end_reason"] == "silence_after_min_utterance"


def test_four_to_eight_second_audio_window_can_enter_stt(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_MIN_UTTERANCE_AUDIO_MS", "4000")
    monkeypatch.setenv("WEBCALL_AI_MAX_UTTERANCE_AUDIO_MS", "8000")
    monkeypatch.setenv("WEBCALL_AI_SILENCE_END_MS", "700")
    frames = [*_repeat(1800, frames=500), *_repeat(0, frames=70)]

    turn = asyncio.run(_collect(frames))

    assert turn.audio_stats is not None
    assert 4000 <= turn.audio_stats["audio_ms"] <= 8000
    assert turn.audio_stats["audio_input_classification"] == "audio_present"
    assert turn.audio_stats["capture_min_audio_ms"] == 4000
    assert turn.audio_stats["capture_max_audio_ms"] == 8000


def test_short_audio_with_rms_continues_collecting_after_early_silence(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_MIN_UTTERANCE_AUDIO_MS", "4000")
    monkeypatch.setenv("WEBCALL_AI_MAX_UTTERANCE_AUDIO_MS", "7000")
    monkeypatch.setenv("WEBCALL_AI_SILENCE_END_MS", "600")
    early_short_audio = [*_repeat(2000, frames=90), *_repeat(0, frames=62)]
    continued_audio = [*_repeat(2000, frames=260), *_repeat(0, frames=60)]

    turn = asyncio.run(_collect([*early_short_audio, *continued_audio]))

    assert turn.audio_stats is not None
    assert turn.audio_stats["audio_ms"] > 1520
    assert turn.audio_stats["frame_count"] > len(early_short_audio)
    assert turn.audio_stats["audio_input_classification"] == "audio_present"


def test_tracking_number_utterance_is_not_truncated_at_pre_number_pause(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_MIN_UTTERANCE_AUDIO_MS", "4000")
    monkeypatch.setenv("WEBCALL_AI_MAX_UTTERANCE_AUDIO_MS", "9000")
    monkeypatch.setenv("WEBCALL_AI_SILENCE_END_MS", "600")
    where_is_my_parcel = _repeat(2100, frames=90)
    pre_tracking_pause = _repeat(0, frames=62)
    tracking_number_segment = _repeat(2100, frames=300)
    final_silence = _repeat(0, frames=60)

    turn = asyncio.run(_collect([*where_is_my_parcel, *pre_tracking_pause, *tracking_number_segment, *final_silence]))

    assert turn.audio_stats is not None
    assert turn.audio_stats["frame_count"] >= len(where_is_my_parcel) + len(pre_tracking_pause) + len(tracking_number_segment)
    assert turn.audio_stats["audio_ms"] >= 4000
    assert turn.audio_stats["capture_mode"] == "tracking_long"
