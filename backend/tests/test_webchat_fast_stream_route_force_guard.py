from pathlib import Path


SRC = Path("backend/app/api/webchat_fast.py").read_text(encoding="utf-8")
CONFIG_SRC = Path("backend/app/services/webchat_fast_config.py").read_text(encoding="utf-8")


def test_stream_disabled_return_is_standard_feature_flag_guarded():
    assert "STREAM_ROUTE_FORCE_ENABLE_BEGIN" not in SRC
    assert "_webchat_stream_route_forced_enabled" not in SRC
    assert "stream_settings = get_webchat_fast_settings()" in SRC
    assert "if not stream_settings.stream_enabled:" in SRC
    assert '"stream_disabled"' in SRC


def test_stream_flag_env_is_defined_in_runtime_config():
    assert "WEBCHAT_FAST_STREAM_ENABLED" in CONFIG_SRC
    assert "WEBCHAT_FAST_STREAM_REQUIRE_ACCEPT" in CONFIG_SRC
    assert "stream_enabled" in CONFIG_SRC
    assert "stream_require_accept" in CONFIG_SRC
