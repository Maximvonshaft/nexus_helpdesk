import argparse
import asyncio
from unittest.mock import AsyncMock

import pytest

from scripts.perf_audit import perf_probe_webchat_fast as probe


def test_probe_wiring_passes_all_flags(monkeypatch):
    spy = AsyncMock()
    # Mocking ProbeResult structure so it doesn't fail downstream logic
    spy.return_value = probe.ProbeResult(
        ok=True,
        first_chunk_ms=10.0,
        total_ms=50.0,
        raw_leak_count=0,
        replayed=False,
        fallback=False,
        stream_success=True,
        stream_error=False,
        error_code=None
    )
    monkeypatch.setattr(probe, "_probe_one", spy)
    
    args = argparse.Namespace(
        base_url="http://127.0.0.1:18081",
        requests=1,
        concurrency=1,
        require_stream=True,
        expect_stream_disabled=True,
        expect_stream_not_in_rollout=True,
        force_stream_canary_header=True
    )
    
    asyncio.run(probe.run_probe(args))
    
    spy.assert_called_once()
    _, kwargs = spy.call_args
    assert kwargs.get("require_stream") is True
    assert kwargs.get("expect_stream_disabled") is True
    assert kwargs.get("expect_stream_not_in_rollout") is True
    assert kwargs.get("force_stream_canary_header") is True

def test_probe_wiring_passes_defaults_when_false(monkeypatch):
    spy = AsyncMock()
    spy.return_value = probe.ProbeResult(
        ok=True,
        first_chunk_ms=10.0,
        total_ms=50.0,
        raw_leak_count=0,
        replayed=False,
        fallback=False,
        stream_success=True,
        stream_error=False,
        error_code=None
    )
    monkeypatch.setattr(probe, "_probe_one", spy)
    
    args = argparse.Namespace(
        base_url="http://127.0.0.1:18081",
        requests=1,
        concurrency=1,
        require_stream=False,
        expect_stream_disabled=False,
        expect_stream_not_in_rollout=False,
        force_stream_canary_header=False
    )
    
    asyncio.run(probe.run_probe(args))
    
    spy.assert_called_once()
    _, kwargs = spy.call_args
    assert kwargs.get("require_stream") is False
    assert kwargs.get("expect_stream_disabled") is False
    assert kwargs.get("expect_stream_not_in_rollout") is False
    assert kwargs.get("force_stream_canary_header") is False

def test_probe_wiring_handles_missing_args(monkeypatch):
    spy = AsyncMock()
    spy.return_value = probe.ProbeResult(
        ok=True,
        first_chunk_ms=10.0,
        total_ms=50.0,
        raw_leak_count=0,
        replayed=False,
        fallback=False,
        stream_success=True,
        stream_error=False,
        error_code=None
    )
    monkeypatch.setattr(probe, "_probe_one", spy)
    
    args = argparse.Namespace(
        base_url="http://127.0.0.1:18081",
        requests=1,
        concurrency=1,
        require_stream=False,
    )
    
    asyncio.run(probe.run_probe(args))
    
    spy.assert_called_once()
    _, kwargs = spy.call_args
    assert kwargs.get("require_stream") is False
    assert kwargs.get("expect_stream_disabled") is False
    assert kwargs.get("expect_stream_not_in_rollout") is False
    assert kwargs.get("force_stream_canary_header") is False