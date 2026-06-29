from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_handoff_snapshot_job_is_claimed_and_processed_by_default_worker():
    source = _read("app/services/background_jobs.py")

    assert "WEBCHAT_HANDOFF_SNAPSHOT_JOB = 'webchat.handoff_snapshot'" in source
    assert "process_webchat_handoff_snapshot_job" in source
    assert "if job.job_type == WEBCHAT_HANDOFF_SNAPSHOT_JOB" in source
    assert "snapshot = payload.get('snapshot')" in source
    assert "process_webchat_handoff_snapshot_job(db, snapshot=snapshot)" in source

    dispatcher = source.split("def dispatch_pending_background_jobs", 1)[1]
    assert "WEBCHAT_HANDOFF_SNAPSHOT_JOB" in dispatcher


def test_stream_parser_emits_safe_content_delta_instead_of_only_inspecting():
    source = _read("app/services/webchat_fast_stream_parser.py")
    feed_event = source.split("def feed_event", 1)[1]
    content_delta_block = feed_event.split("if isinstance(event, Completed)", 1)[0]

    assert "if isinstance(event, ContentDelta):" in content_delta_block
    assert "return self.feed_text(event.text)" in content_delta_block
    assert "self.inspect_text(event.text)" not in content_delta_block


def test_stream_service_retired_provider_fails_closed_without_partial_delta():
    source = _read("app/services/webchat_fast_stream_service.py")
    live_path = source.split('if begin.row_id is None:', 1)[1]

    assert 'yield sse_event("meta", {"replayed": False, "stream_version": "provider_runtime_compat"})' in live_path
    assert '_mark_failed(begin.row_id, "stream_provider_retired")' in live_path
    assert 'record_fast_reply_metric(status="stream_provider_retired", elapsed_ms=0)' in live_path
    assert 'yield sse_event("error", {"error_code": "stream_provider_retired", "retry_after_ms": 1500})' in live_path
    assert 'yield sse_event("reply_delta"' not in live_path
    assert "async for event in" not in source


def test_stream_replay_order_is_replay_then_final_then_delta():
    source = _read("app/services/webchat_fast_stream_service.py")
    replay_path = source.split('if begin.status == "replay":', 1)[1].split('if begin.row_id is None:', 1)[0]

    assert "yield sse_event(\"replay\"" in replay_path
    assert "yield sse_event(\"final\"" in replay_path
    assert "yield sse_event(\"reply_delta\"" in replay_path
    assert replay_path.index("yield sse_event(\"replay\"") < replay_path.index("yield sse_event(\"final\"")
    assert replay_path.index("yield sse_event(\"final\"") < replay_path.index("yield sse_event(\"reply_delta\"")


def test_legacy_webchat_token_transport_is_forced_off_in_production():
    source = _read("app/api/webchat.py")
    legacy_fn = source.split("def _legacy_token_transport_enabled", 1)[1].split("def _resolve_visitor_token", 1)[0]

    assert 'if settings.app_env == "production":' in legacy_fn
    assert "return False" in legacy_fn
    assert "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT" in legacy_fn


def test_legacy_token_resolution_still_prefers_header_token():
    source = _read("app/api/webchat.py")
    resolver = source.split("def _resolve_visitor_token", 1)[1].split("def _hash_token", 1)[0]

    assert "if header_token:" in resolver
    assert "return header_token" in resolver
    assert "body_token or query_token" in resolver
