from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DISCOVERY_PATH = REPO_ROOT / "tools" / "codex-reply-bridge" / "upstream_auth_discovery.py"

spec = importlib.util.spec_from_file_location("codex_upstream_auth_discovery", DISCOVERY_PATH)
assert spec is not None
discovery = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = discovery
assert spec.loader is not None
spec.loader.exec_module(discovery)


def test_discover_auth_profile_oauth_maps_to_chatgpt_auth_tokens(tmp_path: Path):
    auth_file = tmp_path / "auth_profile.json"
    auth_file.write_text(
        """
        {
          "profiles": {
            "openai-codex:default": {
              "type": "oauth",
              "provider": "openai-codex",
              "access": "oauth-access-secret",
              "refresh": "oauth-refresh-secret",
              "accountId": "acct_123",
              "chatgptPlanType": "plus"
            }
          }
        }
        """,
        encoding="utf-8",
    )

    candidate = discovery.discover_auth_profile_file(str(auth_file))
    summary = discovery.auth_source_public_summary(candidate)

    assert summary["usable"] is True
    assert summary["source_kind"] == "auth_profile_file"
    assert summary["credential_kind"] == "oauth"
    assert summary["login_type"] == "chatgptAuthTokens"
    assert summary["account_hint_present"] is True
    assert summary["plan_hint_present"] is True
    assert str(summary["fingerprint"]).startswith("sha256:")
    assert "oauth-access-secret" not in str(summary)
    assert "oauth-refresh-secret" not in str(summary)


def test_discover_auth_profile_api_key_maps_to_api_key_login(tmp_path: Path):
    auth_file = tmp_path / "auth_profile.json"
    auth_file.write_text(
        """
        {
          "profiles": {
            "openai:backup": {
              "type": "api_key",
              "provider": "openai",
              "api_key": "sk-test-secret"
            }
          }
        }
        """,
        encoding="utf-8",
    )

    candidate = discovery.discover_auth_profile_file(str(auth_file))
    summary = discovery.auth_source_public_summary(candidate)

    assert summary["usable"] is True
    assert summary["credential_kind"] == "api_key"
    assert summary["login_type"] == "apiKey"
    assert str(summary["fingerprint"]).startswith("sha256:")
    assert "sk-test-secret" not in str(summary)


def test_discover_codex_cli_auth_file_prefers_access_token(tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        """
        {
          "tokens": {
            "access_token": "cli-access-secret",
            "refresh_token": "cli-refresh-secret"
          },
          "account_id": "acct_cli"
        }
        """,
        encoding="utf-8",
    )

    candidate = discovery.discover_codex_cli_auth_file(str(auth_file))
    summary = discovery.auth_source_public_summary(candidate)

    assert summary["usable"] is True
    assert summary["source_kind"] == "codex_cli_auth_file"
    assert summary["credential_kind"] == "token"
    assert summary["login_type"] == "chatgptAuthTokens"
    assert summary["account_hint_present"] is True
    assert str(summary["fingerprint"]).startswith("sha256:")
    assert "cli-access-secret" not in str(summary)
    assert "cli-refresh-secret" not in str(summary)


def test_discover_api_key_file_maps_to_api_key_without_echo(tmp_path: Path):
    api_key_file = tmp_path / "api_key"
    api_key_file.write_text("Bearer sk-file-secret", encoding="utf-8")

    candidate = discovery.discover_api_key_file(str(api_key_file))
    summary = discovery.auth_source_public_summary(candidate)

    assert summary["usable"] is True
    assert summary["source_kind"] == "api_key_file"
    assert summary["credential_kind"] == "api_key"
    assert summary["login_type"] == "apiKey"
    assert str(summary["fingerprint"]).startswith("sha256:")
    assert "sk-file-secret" not in str(summary)


def test_select_best_auth_source_priority(tmp_path: Path):
    auth_file = tmp_path / "auth_profile.json"
    cli_file = tmp_path / "auth.json"
    api_key_file = tmp_path / "api_key"
    auth_file.write_text(
        '{"profiles":{"p":{"type":"token","provider":"openai-codex","access":"profile-secret"}}}',
        encoding="utf-8",
    )
    cli_file.write_text('{"access_token":"cli-secret"}', encoding="utf-8")
    api_key_file.write_text("sk-file-secret", encoding="utf-8")

    candidates = discovery.discover_auth_sources(
        auth_profile_file=str(auth_file),
        codex_cli_auth_file=str(cli_file),
        api_key_file=str(api_key_file),
    )
    selected = discovery.select_best_auth_source(candidates)

    assert selected.source_kind == "auth_profile_file"
    assert selected.usable is True
    assert selected.login_type == "chatgptAuthTokens"


def test_discovery_handles_missing_sources_safely():
    candidates = discovery.discover_auth_sources(
        auth_profile_file=None,
        codex_cli_auth_file=None,
        api_key_file=None,
    )
    selected = discovery.select_best_auth_source(candidates)
    summary = discovery.auth_source_public_summary(selected)

    assert summary == {
        "source_kind": "none",
        "path_present": False,
        "usable": False,
        "credential_kind": "none",
        "login_type": "none",
        "account_hint_present": False,
        "plan_hint_present": False,
        "fingerprint": None,
        "error_code": "no_usable_auth_source",
    }
