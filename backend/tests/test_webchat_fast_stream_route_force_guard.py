from pathlib import Path


SRC = Path("backend/app/api/webchat_fast.py").read_text(encoding="utf-8")


def test_stream_disabled_return_is_force_guarded():
    assert "STREAM_ROUTE_FORCE_ENABLE_BEGIN" in SRC
    assert "_webchat_stream_route_forced_enabled" in SRC

    for idx, line in enumerate(SRC.splitlines()):
        if "stream_disabled" in line:
            context = "\n".join(SRC.splitlines()[max(0, idx - 8): idx + 1])
            assert "_webchat_stream_route_forced_enabled" in context


def test_stream_force_env_aliases_are_present():
    for key in [
        "WEBCHAT_FAST_REPLY_STREAM_ENABLED",
        "WEBCHAT_FAST_REPLY_STREAMING_ENABLED",
        "WEBCHAT_FAST_REPLY_STREAM_ROUTE_ENABLED",
        "WEBCHAT_FAST_STREAM_ENABLED",
        "WEBCHAT_STREAM_ENABLED",
        "WEBCHAT_STREAMING_ENABLED",
        "WEBCHAT_ENABLE_STREAM",
    ]:
        assert key in SRC
