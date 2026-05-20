from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_PATH = REPO_ROOT / "tools" / "codex-reply-bridge" / "upstream_login_payload_boundary.py"

spec = importlib.util.spec_from_file_location("codex_upstream_login_payload_boundary", BOUNDARY_PATH)
assert spec is not None
boundary = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = boundary
assert spec.loader is not None
spec.loader.exec_module(boundary)


def test_auth_profile_token_builds_chatgpt_payload_without_safe_value(tmp_path: Path):
    auth_file = tmp_path / "auth_profile.json"
    sample_value = "sample-alpha-value"
    auth_file.write_text(
        """
        {
          "profiles": {
            "openai-codex:default": {
              "type": "token",
              "provider": "openai-codex",
              "access": "sample-alpha-value",
              "accountId": "acct_profile",
              "chatgptPlanType": "plus"
            }
          }
        }
        """,
        encoding="utf-8",
    )

    result = boundary.build_login_payload_from_auth_profile_file(str(auth_file))
    summary = boundary.login_payload_safe_summary(result)

    assert result.payload == {
        "type": "chatgptAuthTokens",
        "accessToken": sample_value,
        "chatgptAccountId": "acct_profile",
        "chatgptPlanType": "plus",
    }
    assert result.login_type == "chatgptAuthTokens"
    assert summary["payload_ready"] is True
    assert summary["login_type"] == "chatgptAuthTokens"
    assert summary["chatgpt_account_id_present"] is True
    assert summary["chatgpt_plan_type_present"] is True
    assert str(summary["secret_fingerprint"]).startswith("sha256:")
    assert sample_value not in str(summary)


def test_auth_profile_api_key_builds_api_key_payload_without_safe_value(tmp_path: Path):
    auth_file = tmp_path / "auth_profile.json"
    sample_value = "sample-beta-value"
    auth_file.write_text(
        """
        {
          "profiles": {
            "openai:backup": {
              "type": "api_key",
              "provider": "openai",
              "api_key": "sample-beta-value"
            }
          }
        }
        """,
        encoding="utf-8",
    )

    result = boundary.build_login_payload_from_auth_profile_file(str(auth_file))
    summary = boundary.login_payload_safe_summary(result)

    assert result.payload == {"type": "apiKey", "apiKey": sample_value}
    assert result.login_type == "apiKey"
    assert summary["payload_ready"] is True
    assert summary["login_type"] == "apiKey"
    assert str(summary["secret_fingerprint"]).startswith("sha256:")
    assert sample_value not in str(summary)


def test_cli_file_access_token_builds_chatgpt_payload(tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    sample_value = "sample-gamma-value"
    auth_file.write_text(
        """
        {
          "tokens": {
            "access_token": "sample-gamma-value"
          },
          "chatgptAccountId": "acct_cli",
          "chatgptPlanType": "team"
        }
        """,
        encoding="utf-8",
    )

    result = boundary.build_login_payload_from_codex_cli_auth_file(str(auth_file))
    summary = boundary.login_payload_safe_summary(result)

    assert result.payload == {
        "type": "chatgptAuthTokens",
        "accessToken": sample_value,
        "chatgptAccountId": "acct_cli",
        "chatgptPlanType": "team",
    }
    assert summary["payload_ready"] is True
    assert sample_value not in str(summary)


def test_api_key_file_builds_api_key_payload_without_safe_value(tmp_path: Path):
    key_file = tmp_path / "api_key"
    sample_value = "sample-delta-value"
    key_file.write_text(sample_value, encoding="utf-8")

    result = boundary.build_login_payload_from_api_key_file(str(key_file))
    summary = boundary.login_payload_safe_summary(result)

    assert result.payload == {"type": "apiKey", "apiKey": sample_value}
    assert result.login_type == "apiKey"
    assert summary["payload_ready"] is True
    assert sample_value not in str(summary)


def test_best_login_payload_uses_priority_order(tmp_path: Path):
    auth_file = tmp_path / "auth_profile.json"
    cli_file = tmp_path / "auth.json"
    api_key_file = tmp_path / "api_key"
    auth_file.write_text('{"profiles":{"p":{"type":"token","provider":"openai-codex","access":"value-one"}}}', encoding="utf-8")
    cli_file.write_text('{"access_token":"value-two"}', encoding="utf-8")
    api_key_file.write_text("value-three", encoding="utf-8")

    result = boundary.build_best_login_payload(
        auth_profile_file=str(auth_file),
        codex_cli_auth_file=str(cli_file),
        api_key_file=str(api_key_file),
    )

    assert result.source_kind == "auth_profile_file"
    assert result.payload["accessToken"] == "value-one"  # type: ignore[index]


def test_no_usable_login_payload_is_safe():
    result = boundary.build_best_login_payload(
        auth_profile_file=None,
        codex_cli_auth_file=None,
        api_key_file=None,
    )
    summary = boundary.login_payload_safe_summary(result)

    assert result.payload is None
    assert result.error_code == "no_usable_login_payload"
    assert summary == {
        "source_kind": "none",
        "login_type": "none",
        "payload_ready": False,
        "secret_fingerprint": None,
        "chatgpt_account_id_present": False,
        "chatgpt_plan_type_present": False,
        "error_code": "no_usable_login_payload",
    }
