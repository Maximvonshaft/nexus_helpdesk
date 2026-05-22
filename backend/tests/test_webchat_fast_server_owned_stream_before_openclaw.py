from pathlib import Path


SRC = Path("backend/app/api/webchat_fast.py").read_text(encoding="utf-8")


def _stream_route() -> str:
    start = SRC.index('@router.post("/fast-reply/stream")')
    end = SRC.find("\n@router.", start + 1)
    return SRC[start:end if end != -1 else len(SRC)]


def _server_owned_block() -> str:
    route = _stream_route()
    return route[
        route.index("SERVER_OWNED_STREAM_BEFORE_OPENCLAW_SETTINGS_BEGIN"):
        route.index("SERVER_OWNED_STREAM_BEFORE_OPENCLAW_SETTINGS_END")
    ]


def test_server_owned_stream_block_runs_before_openclaw_upstream_check():
    route = _stream_route()
    block_idx = route.index("SERVER_OWNED_STREAM_BEFORE_OPENCLAW_SETTINGS_BEGIN")
    upstream_idx = route.index("stream_upstream_not_configured")
    rollout_idx = route.index("is_stream_rollout_selected")

    assert block_idx < upstream_idx
    assert block_idx < rollout_idx


def test_server_owned_stream_block_uses_existing_route_state_not_db_injection():
    block = _server_owned_block()

    assert "_server_policy_stream_events(" in block
    assert "_tracking_fact_forced_stream_events(" in block
    assert "_tracking_candidate_selection_stream_events(" in block
    assert "context_payload=merged_context" in block
    assert "routing_context=routing_context" in block

    assert "SessionLocal()" not in block
    assert "get_db()" not in block
    assert "owned_db" not in block
    assert "_extract_tracking_number" not in block


def test_generic_stream_still_requires_openclaw_after_server_owned_paths():
    route = _stream_route()
    block_end = route.index("SERVER_OWNED_STREAM_BEFORE_OPENCLAW_SETTINGS_END")
    upstream_idx = route.index("stream_upstream_not_configured")

    assert block_end < upstream_idx
