from __future__ import annotations

from app.services.webchat_fast_config import WebchatFastSettings
from app.services.webchat_openclaw_responses_client import build_responses_request_body


def _settings() -> WebchatFastSettings:
    return WebchatFastSettings(
        enabled=True,
        provider="openclaw_responses",
        timeout_ms=3000,
        max_timeout_ms=5000,
        history_turns=5,
        max_prompt_chars=2500,
        rate_limit_window_seconds=60,
        rate_limit_max_requests=30,
        hard_fail_on_non_ai_reply=True,
        openclaw_responses_url="http://openclaw-gateway-private:18789/v1/responses",
        openclaw_responses_agent_id="webchat-fast",
        openclaw_responses_token_file=None,
        openclaw_responses_token="local-token",
        openclaw_connect_timeout_ms=500,
        openclaw_read_timeout_ms=3000,
        openclaw_total_timeout_ms=3500,
        openclaw_pool_max_connections=10,
        openclaw_pool_max_keepalive=5,
        app_env="test",
    )


def test_build_responses_body_selects_agent_by_model_field():
    body = build_responses_request_body(instructions="Return JSON only", input_text="Customer message: Hi", settings=_settings())

    assert body["model"] == "openclaw:webchat-fast"
    assert body["stream"] is False
    assert body["max_output_tokens"] == 350
    assert "instructions" in body
    assert body["input"][0]["role"] == "user"
    assert body["input"][0]["content"][0]["type"] == "input_text"


def test_local_dev_token_property_allows_env_token():
    settings = _settings()

    assert settings.token == "local-token"
    assert settings.is_openclaw_configured is True


def test_disabled_fast_ai_does_not_require_token_or_url():
    settings = WebchatFastSettings(
        enabled=False,
        provider="openclaw_responses",
        timeout_ms=3000,
        max_timeout_ms=5000,
        history_turns=5,
        max_prompt_chars=2500,
        rate_limit_window_seconds=60,
        rate_limit_max_requests=30,
        hard_fail_on_non_ai_reply=True,
        openclaw_responses_url="",
        openclaw_responses_agent_id="webchat-fast",
        openclaw_responses_token_file=None,
        openclaw_responses_token=None,
        openclaw_connect_timeout_ms=500,
        openclaw_read_timeout_ms=3000,
        openclaw_total_timeout_ms=3500,
        openclaw_pool_max_connections=10,
        openclaw_pool_max_keepalive=5,
        app_env="production",
    )

    settings.validate_runtime()
    assert settings.is_openclaw_configured is False
