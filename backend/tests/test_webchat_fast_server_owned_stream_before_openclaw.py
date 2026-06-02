from pathlib import Path


SRC = Path("backend/app/api/webchat_fast.py").read_text(encoding="utf-8")


def _stream_route() -> str:
    start = SRC.index('@router.post("/fast-reply/stream")')
    end = SRC.find("\n@router.", start + 1)
    return SRC[start:end if end != -1 else len(SRC)]


def test_stream_route_no_longer_runs_server_owned_policy_before_ai_runtime():
    route = _stream_route()

    assert "SERVER_OWNED_STREAM_BEFORE_OPENCLAW_SETTINGS_BEGIN" not in route
    assert "stream_upstream_not_configured" not in route
    assert "is_stream_rollout_selected" not in route
    assert "decide_server_handoff_policy" not in route
    assert "_stream_process_events" in route
    assert "prepare_webchat_fast_stream" in route


def test_stream_runtime_uses_existing_idempotency_state_and_shared_processor():
    route = _stream_route()

    assert "prepare_webchat_fast_stream(" in route
    assert "_stream_begin_status_response(begin, headers)" in route
    assert "_stream_replay_events(" in route
    assert "_stream_process_events(row_id=begin.row_id" in route
    assert "_process_fast_reply(row_id=row_id" in SRC


def test_generic_stream_path_uses_ai_decision_runtime_contract():
    assert "V3.ai_decision_runtime" in SRC
    assert "decision_runtime" in SRC
    assert "webchat_ai_decision_v1" in SRC
    assert "ai_decision_trace" in SRC
