#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


AuthSourceKind = Literal["auth_profile_file", "codex_cli_auth_file", "api_key_file", "none"]
CredentialKind = Literal["oauth", "token", "api_key", "unknown", "none"]
LoginType = Literal["chatgptAuthTokens", "apiKey", "none"]


@dataclass(frozen=True)
class AuthSourceCandidate:
    source_kind: AuthSourceKind
    path_present: bool
    usable: bool
    credential_kind: CredentialKind
    login_type: LoginType
    account_hint_present: bool
    plan_hint_present: bool
    fingerprint: str | None
    error_code: str | None = None


def _safe_read_text(path_value: str | None, *, max_bytes: int = 512_000) -> tuple[str | None, str | None]:
    if not path_value:
        return None, "path_missing"
    path = Path(path_value)
    if not path.is_file():
        return None, "path_not_found"
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "path_unreadable"
    if len(raw) > max_bytes:
        return None, "file_too_large"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "file_not_utf8"


def _load_json_file(path_value: str | None) -> tuple[Any | None, str | None]:
    text, error = _safe_read_text(path_value)
    if error:
        return None, error
    try:
        return json.loads(text or ""), None
    except json.JSONDecodeError:
        return None, "json_invalid"


def _fingerprint_secret(value: str, *, purpose: str) -> str:
    secret = value.strip()
    if not secret:
        return ""
    digest = hashlib.sha256((purpose + "\0" + secret).encode("utf-8", errors="ignore")).hexdigest()
    return "sha256:" + digest


def _deep_find_first_string(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _deep_find_first_string(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _deep_find_first_string(item, keys)
            if found:
                return found
    return None


def _deep_find_profile(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        credential_type = str(value.get("type") or "").strip().lower()
        provider = str(value.get("provider") or "").strip().lower()
        if credential_type in {"oauth", "token", "api_key"} and provider in {"openai-codex", "openai"}:
            return value
        profiles = value.get("profiles")
        if isinstance(profiles, dict):
            for profile in profiles.values():
                found = _deep_find_profile(profile)
                if found:
                    return found
        for item in value.values():
            found = _deep_find_profile(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _deep_find_profile(item)
            if found:
                return found
    return None


def _account_hint_present(value: Any) -> bool:
    return bool(_deep_find_first_string(value, {"account_id", "accountid", "chatgpt_account_id", "email"}))


def _plan_hint_present(value: Any) -> bool:
    return bool(_deep_find_first_string(value, {"chatgpt_plan_type", "plantype", "plan_type"}))


def _profile_secret_for_kind(profile: dict[str, Any], credential_kind: CredentialKind) -> str | None:
    if credential_kind == "api_key":
        return _deep_find_first_string(profile, {"api_key", "apikey", "key"})
    if credential_kind in {"oauth", "token"}:
        return _deep_find_first_string(profile, {"access", "access_token", "accesstoken", "token", "api_key", "apikey"})
    return None


def discover_auth_profile_file(path_value: str | None) -> AuthSourceCandidate:
    value, error = _load_json_file(path_value)
    if error:
        return AuthSourceCandidate("auth_profile_file", path_present=error not in {"path_missing", "path_not_found"}, usable=False, credential_kind="none", login_type="none", account_hint_present=False, plan_hint_present=False, fingerprint=None, error_code=error)
    profile = _deep_find_profile(value)
    if not profile:
        return AuthSourceCandidate("auth_profile_file", True, False, "unknown", "none", _account_hint_present(value), _plan_hint_present(value), None, "profile_not_found")
    credential_kind = str(profile.get("type") or "unknown").strip().lower()
    if credential_kind not in {"oauth", "token", "api_key"}:
        credential_kind = "unknown"
    login_type: LoginType = "apiKey" if credential_kind == "api_key" else "chatgptAuthTokens" if credential_kind in {"oauth", "token"} else "none"
    secret = _profile_secret_for_kind(profile, credential_kind)  # type: ignore[arg-type]
    fingerprint = _fingerprint_secret(secret, purpose="nexus:codex:auth-profile:v1") if secret else None
    return AuthSourceCandidate(
        "auth_profile_file",
        True,
        bool(secret and login_type != "none"),
        credential_kind,  # type: ignore[arg-type]
        login_type,
        _account_hint_present(profile),
        _plan_hint_present(profile),
        fingerprint,
        None if secret else "credential_secret_missing",
    )


def discover_codex_cli_auth_file(path_value: str | None) -> AuthSourceCandidate:
    value, error = _load_json_file(path_value)
    if error:
        return AuthSourceCandidate("codex_cli_auth_file", path_present=error not in {"path_missing", "path_not_found"}, usable=False, credential_kind="none", login_type="none", account_hint_present=False, plan_hint_present=False, fingerprint=None, error_code=error)
    access_token = _deep_find_first_string(value, {"access_token", "accesstoken", "access", "token"})
    api_key = _deep_find_first_string(value, {"api_key", "apikey", "openai_api_key", "codex_api_key"})
    if access_token:
        return AuthSourceCandidate(
            "codex_cli_auth_file",
            True,
            True,
            "token",
            "chatgptAuthTokens",
            _account_hint_present(value),
            _plan_hint_present(value),
            _fingerprint_secret(access_token, purpose="nexus:codex:cli-access-token:v1"),
        )
    if api_key:
        return AuthSourceCandidate(
            "codex_cli_auth_file",
            True,
            True,
            "api_key",
            "apiKey",
            _account_hint_present(value),
            _plan_hint_present(value),
            _fingerprint_secret(api_key, purpose="nexus:codex:cli-api-key:v1"),
        )
    return AuthSourceCandidate("codex_cli_auth_file", True, False, "unknown", "none", _account_hint_present(value), _plan_hint_present(value), None, "credential_secret_missing")


def discover_api_key_file(path_value: str | None) -> AuthSourceCandidate:
    text, error = _safe_read_text(path_value, max_bytes=64_000)
    if error:
        return AuthSourceCandidate("api_key_file", path_present=error not in {"path_missing", "path_not_found"}, usable=False, credential_kind="none", login_type="none", account_hint_present=False, plan_hint_present=False, fingerprint=None, error_code=error)
    api_key = (text or "").strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key.split(None, 1)[1].strip()
    if not api_key:
        return AuthSourceCandidate("api_key_file", True, False, "api_key", "apiKey", False, False, None, "credential_secret_missing")
    return AuthSourceCandidate(
        "api_key_file",
        True,
        True,
        "api_key",
        "apiKey",
        False,
        False,
        _fingerprint_secret(api_key, purpose="nexus:codex:api-key-file:v1"),
    )


def discover_auth_sources(*, auth_profile_file: str | None, codex_cli_auth_file: str | None, api_key_file: str | None) -> list[AuthSourceCandidate]:
    return [
        discover_auth_profile_file(auth_profile_file),
        discover_codex_cli_auth_file(codex_cli_auth_file),
        discover_api_key_file(api_key_file),
    ]


def select_best_auth_source(candidates: list[AuthSourceCandidate]) -> AuthSourceCandidate:
    for candidate in candidates:
        if candidate.usable:
            return candidate
    return AuthSourceCandidate("none", False, False, "none", "none", False, False, None, "no_usable_auth_source")


def auth_source_public_summary(candidate: AuthSourceCandidate) -> dict[str, Any]:
    return {
        "source_kind": candidate.source_kind,
        "path_present": candidate.path_present,
        "usable": candidate.usable,
        "credential_kind": candidate.credential_kind,
        "login_type": candidate.login_type,
        "account_hint_present": candidate.account_hint_present,
        "plan_hint_present": candidate.plan_hint_present,
        "fingerprint": candidate.fingerprint,
        "error_code": candidate.error_code,
    }
