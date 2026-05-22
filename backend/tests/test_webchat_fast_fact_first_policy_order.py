from pathlib import Path


SRC = Path("backend/app/api/webchat_fast.py").read_text(encoding="utf-8")


def _section(start: str, end: str | None = None) -> str:
    s = SRC.index(start)
    e = SRC.index(end, s) if end else len(SRC)
    return SRC[s:e]


def test_non_stream_fact_first_does_not_precede_server_handoff_policy():
    section = _section(
        '@router.post("/fast-reply")',
        '@router.post("/fast-reply/stream")',
    )

    assert "FACT_FIRST_TRACKING_NON_STREAM_BEGIN" not in section
    assert "FACT_FIRST_TRACKING_NON_STREAM_END" not in section

    policy_idx = section.index("server_policy = decide_server_handoff_policy")
    lookup_idx = section.index("tracking_fact = _lookup_fast_tracking_fact")

    assert policy_idx < lookup_idx


def test_stream_fact_first_does_not_precede_server_handoff_policy():
    section = _section('@router.post("/fast-reply/stream")')

    assert "FACT_FIRST_TRACKING_STREAM_BEGIN" not in section
    assert "FACT_FIRST_TRACKING_STREAM_END" not in section

    policy_idx = section.index("server_policy = decide_server_handoff_policy")
    lookup_idx = section.index("tracking_fact = _lookup_fast_tracking_fact")

    assert policy_idx < lookup_idx


def test_forced_tracking_fact_reply_still_exists_after_policy_path():
    assert "_tracking_fact_forced_reply_payload" in SRC
    assert 'reply_source": "server_tracking_fact"' in SRC
    assert "server_policy = decide_server_handoff_policy" in SRC
