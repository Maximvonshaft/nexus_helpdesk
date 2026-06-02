from pathlib import Path


SRC = Path("backend/app/api/webchat_fast.py").read_text(encoding="utf-8")


def _section(start: str, end: str | None = None) -> str:
    s = SRC.index(start)
    e = SRC.index(end, s) if end else len(SRC)
    return SRC[s:e]


def test_non_stream_uses_unified_ai_decision_runtime_processor():
    section = _section(
        '@router.post("/fast-reply")',
        '@router.post("/fast-reply/stream")',
    )

    assert "server_policy = decide_server_handoff_policy" not in section
    assert "_tracking_fact_forced_reply_payload" not in section
    assert "_process_fast_reply(row_id=row_id" in section
    assert "begin_webchat_fast_idempotency" in section


def test_stream_uses_same_decision_runtime_processor_as_non_stream():
    section = _section('@router.post("/fast-reply/stream")')

    assert "server_policy = decide_server_handoff_policy" not in section
    assert "_tracking_fact_forced_stream_events" not in section
    assert "_stream_process_events" in section
    assert "prepare_webchat_fast_stream" in section
    assert "ai_decision_trace" in SRC


def test_tracking_fact_is_evidence_source_not_server_owned_final_reply():
    assert "_tracking_fact_public_payload" in SRC
    assert "_tracking_fact_evidence_trace" in SRC
    assert "speedaf_trusted_tracking_fact" in SRC
    assert '"server_tracking_fact"' not in SRC
    assert "_tracking_fact_forced_reply_payload" not in SRC
