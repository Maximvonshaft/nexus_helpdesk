from app.services.provider_runtime.router import (
    _bounded_provider_error_code,
    _bounded_provider_summary,
)


def test_provider_error_codes_collapse_to_fixed_categories():
    assert _bounded_provider_error_code("private_ai_runtime_timeout") == "provider_timeout"
    assert _bounded_provider_error_code("private_ai_runtime_http_503") == "provider_http_error"
    assert _bounded_provider_error_code("private_ai_runtime_network_error") == "provider_network_error"
    assert _bounded_provider_error_code("private_ai_runtime_token_missing") == "provider_configuration_error"
    assert _bounded_provider_error_code("private_ai_runtime_bad_response") == "provider_output_invalid"
    assert _bounded_provider_error_code("customer supplied arbitrary text") == "provider_call_failed"


def test_provider_summary_keeps_only_bounded_structural_diagnostics():
    summary = _bounded_provider_summary(
        {
            "provider": "private_ai_runtime",
            "endpoint_path": "/api/chat",
            "model": "qwen2.5:3b",
            "prompt_chars": 512,
            "token_file_configured": True,
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "customer_text": "must-not-cross",
            },
            "reason": "upstream returned customer-controlled text",
            "error_code": "private_ai_runtime_http_500",
            "prompt": "secret customer prompt",
            "raw_payload": {"customer_reply": "secret"},
        }
    )

    assert summary == {
        "provider": "private_ai_runtime",
        "endpoint_path": "/api/chat",
        "model": "qwen2.5:3b",
        "prompt_chars": 512,
        "token_file_configured": True,
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
        },
    }
    assert "must-not-cross" not in str(summary)
    assert "secret" not in str(summary)
