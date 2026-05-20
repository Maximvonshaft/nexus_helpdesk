#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


LoginType = Literal["chatgptAuthTokens", "apiKey", "none"]
SourceKind = Literal["auth_profile_file", "codex_cli_auth_file", "api_key_file", "none"]


@dataclass(frozen=True)
class LoginPayloadBoundaryResult:
    source_kind: SourceKind
    login_type: LoginType
    payload: dict[str, Any] | None
    safe_summary: dict[str, Any]
    error_code: str | None = None


def _read_text(path_value: str | None, *, max_bytes: int = 512_000) -> tuple[str | None, str | None]:
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


def _read_json(path_value: str | None) -> tuple[Any | None, str | None]:
    text, error = _read_text(path_value)
    if error:
        return None, error
    try:
        return json.loads(text or "")
    except json.JSONDecodeError:
        return None, "json_invalid"


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _find_first_string(value: Any, keys: set[str]) -> str | None:
    normalized_keys = {_normalize_key(key) for key in keys}
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalize_key(str(key)) in normalized_keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _find_first_string(item, normalized_keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_string(item, normalized_keys)
            if found:
                return found
    return None


def _find_profile(value: Any) -> tuple[str | None, dict[str, Any] | None]:
    if isinstance(value, dict):
        credential_type = str(value.get("type") or "").strip().lower()
        provider = str(value.get("provider") or "").strip().lower()
        if credential_type in {"oauth", "token", "api_key"} and provider in {"openai-codex", "openai"}:
            return None, value
        profiles = value.get("profiles")
        if isinstance(profiles, dict):
            for profile_id, profile in profiles.items():
                _, found = _find_profile(profile)
                if found:
                    return str(profile_id), found
        for item in value.values():
            profile_id, found = _find_profile(item)
            if found:
                return profile_id, found
    elif isinstance(value, list):
        for item in value:
            profile_id, found = _find_profile(item)
            if found:
                return profile_id, found
    return None, None


def _fingerprint_secret(value: str, *, purpose: str) -> str:
    digest = hashlib.sha256((purpose + "\0" + value.strip()).encode("utf-8", errors="ignore")).hexdigest()
    return "sha256:" + digest


def _safe_summary(
    *,
    source_kind: SourceKind,
    login_type: LoginType,
    secret: str | None,
    chatgpt_account_id: str | None = None,
    chatgpt_plan_type: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "login_type": login_type,
        "payload_ready": bool(secret and login_type != "none"),
        "secret_fingerprint": _fingerprint_secret(secret, purpose="nexus:codex:login-payload-boundary:v1") if secret else None,
        "chatgpt_account_id_present": bool(chatgpt_account_id),
        "chatgpt_plan_type_present": bool(chatgpt_plan_type),
        "error_code": error_code,
    }


def _profile_access_token(profile: dict[str, Any]) -> str | None:
    return _find_first_string(profile, {"access", "access_token", "accesstoken", "token", "api_key", "apikey"})


def _profile_api_key(profile: dict[str, Any]) -> str | None:
    return _find_first_string(profile, {"api_key", "apikey", "key"})


def _profile_account_id(profile_id: str | None, profile: dict[str, Any]) -> str:
    account_id = _find_first_string(profile, {"account_id", "accountid", "chatgpt_account_id", "chatgptaccountid"})
    if account_id:
        return account_id
    email = _find_first_string(profile, {"email"})
    if email:
        return email
    return profile_id or "openai-codex:default"


def _profile_plan_type(profile: dict[str, Any]) -> str | None:
    return _find_first_string(profile, {"chatgpt_plan_type", "chatgptplantype", "plantype", "plan_type"})


def build_login_payload_from_auth_profile_file(path_value: str | None) -> LoginPayloadBoundaryResult:
    value, error = _read_json(path_value)
    if error:
        return LoginPayloadBoundaryResult("auth_profile_file", "none", None, _safe_summary(source_kind="auth_profile_file", login_type="none", secret=None, error_code=error), error)
    profile_id, profile = _find_profile(value)
    if not profile:
        return LoginPayloadBoundaryResult("auth_profile_file", "none", None, _safe_summary(source_kind="auth_profile_file", login_type="none", secret=None, error_code="profile_not_found"), "profile_not_found")
    credential_type = str(profile.get("type") or "").strip().lower()
    if credential_type == "api_key":
        api_key = _profile_api_key(profile)
        if not api_key:
            return LoginPayloadBoundaryResult("auth_profile_file", "apiKey", None, _safe_summary(source_kind="auth_profile_file", login_type="apiKey", secret=None, error_code="credential_secret_missing"), "credential_secret_missing")
        payload = {"type": "apiKey", "apiKey": api_key}
        return LoginPayloadBoundaryResult("auth_profile_file", "apiKey", payload, _safe_summary(source_kind="auth_profile_file", login_type="apiKey", secret=api_key))
    if credential_type in {"oauth", "token"}:
        access_token = _profile_access_token(profile)
        account_id = _profile_account_id(profile_id, profile)
        plan_type = _profile_plan_type(profile)
        if not access_token:
            return LoginPayloadBoundaryResult("auth_profile_file", "chatgptAuthTokens", None, _safe_summary(source_kind="auth_profile_file", login_type="chatgptAuthTokens", secret=None, chatgpt_account_id=account_id, chatgpt_plan_type=plan_type, error_code="credential_secret_missing"), "credential_secret_missing")
        payload = {
            "type": "chatgptAuthTokens",
            "accessToken": access_token,
            "chatgptAccountId": account_id,
            "chatgptPlanType": plan_type,
        }
        return LoginPayloadBoundaryResult("auth_profile_file", "chatgptAuthTokens", payload, _safe_summary(source_kind="auth_profile_file", login_type="chatgptAuthTokens", secret=access_token, chatgpt_account_id=account_id, chatgpt_plan_type=plan_type))
    return LoginPayloadBoundaryResult("auth_profile_file", "none", None, _safe_summary(source_kind="auth_profile_file", login_type="none", secret=None, error_code="unsupported_credential_type"), "unsupported_credential_type")


def build_login_payload_from_codex_cli_auth_file(path_value: str | None) -> LoginPayloadBoundaryResult:
    value, error = _read_json(path_value)
    if error:
        return LoginPayloadBoundaryResult("codex_cli_auth_file", "none", None, _safe_summary(source_kind="codex_cli_auth_file", login_type="none", secret=None, error_code=error), error)
    access_token = _find_first_string(value, {"access", "access_token", "accesstoken", "token"})
    account_id = _find_first_string(value, {"account_id", "accountid", "chatgpt_account_id", "chatgptaccountid", "email"}) or "codex-cli-auth"
    plan_type = _find_first_string(value, {"chatgpt_plan_type", "chatgptplantype", "plantype", "plan_type"})
    if access_token:
        payload = {
            "type": "chatgptAuthTokens",
            "accessToken": access_token,
            "chatgptAccountId": account_id,
            "chatgptPlanType": plan_type,
        }
        return LoginPayloadBoundaryResult("codex_cli_auth_file", "chatgptAuthTokens", payload, _safe_summary(source_kind="codex_cli_auth_file", login_type="chatgptAuthTokens", secret=access_token, chatgpt_account_id=account_id, chatgpt_plan_type=plan_type))
    api_key = _find_first_string(value, {"api_key", "apikey", "openai_api_key", "codex_api_key"})
    if api_key:
        payload = {"type": "apiKey", "apiKey": api_key}
        return LoginPayloadBoundaryResult("codex_cli_auth_file", "apiKey", payload, _safe_summary(source_kind="codex_cli_auth_file", login_type="apiKey", secret=api_key))
    return LoginPayloadBoundaryResult("codex_cli_auth_file", "none", None, _safe_summary(source_kind="codex_cli_auth_file", login_type="none", secret=None, error_code="credential_secret_missing"), "credential_secret_missing")


def build_login_payload_from_api_key_file(path_value: str | None) -> LoginPayloadBoundaryResult:
    text, error = _read_text(path_value, max_bytes=64_000)
    if error:
        return LoginPayloadBoundaryResult("api_key_file", "none", None, _safe_summary(source_kind="api_key_file", login_type="none", secret=None, error_code=error), error)
    api_key = (text or "").strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key.split(None, 1)[1].strip()
    if not api_key:
        return LoginPayloadBoundaryResult("api_key_file", "apiKey", None, _safe_summary(source_kind="api_key_file", login_type="apiKey", secret=None, error_code="credential_secret_missing"), "credential_secret_missing")
    payload = {"type": "apiKey", "apiKey": api_key}
    return LoginPayloadBoundaryResult("api_key_file", "apiKey", payload, _safe_summary(source_kind="api_key_file", login_type="apiKey", secret=api_key))


def build_best_login_payload(
    *,
    auth_profile_file: str | None,
    codex_cli_auth_file: str | None,
    api_key_file: str | None,
) -> LoginPayloadBoundaryResult:
    for builder, path_value in (
        (build_login_payload_from_auth_profile_file, auth_profile_file),
        (build_login_payload_from_codex_cli_auth_file, codex_cli_auth_file),
        (build_login_payload_from_api_key_file, api_key_file),
    ):
        result = builder(path_value)
        if result.payload:
            return result
    summary = _safe_summary(source_kind="none", login_type="none", secret=None, error_code="no_usable_login_payload")
    return LoginPayloadBoundaryResult("none", "none", None, summary, "no_usable_login_payload")


def login_payload_safe_summary(result: LoginPayloadBoundaryResult) -> dict[str, Any]:
    return dict(result.safe_summary)
