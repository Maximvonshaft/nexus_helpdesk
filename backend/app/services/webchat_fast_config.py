from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


_ALLOWED_FAST_AI_PROVIDERS = {"codex_auth", "codex_app_server", "openai_responses", "provider_runtime"}
_PRODUCTION_FAST_AI_PROVIDER = "provider_runtime"
_PRODUCTION_FORBIDDEN_DIRECT_PROVIDERS = {"codex_auth", "codex_app_server", "openai_responses"}
_ALLOWED_FAST_AI_FALLBACK_PROVIDERS = {"openai_responses", "rule_engine", "none"}
_ALLOWED_TRACKING_DEDUPE_SCOPES = {"legacy", "tenant_channel", "tenant_channel_customer"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _read_secret_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    try:
        value = Path(path_value).read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    return value or None


def _normalize_secret_value(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned.split(None, 1)[1].strip()
    return cleaned or None


@dataclass(frozen=True)
class WebchatFastSettings:
    enabled: bool
    provider: str
    fallback_provider: str
    codex_enabled: bool
    codex_app_server_enabled: bool
    openai_enabled: bool
    timeout_ms: int
    max_timeout_ms: int
    history_turns: int
    max_prompt_chars: int
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    hard_fail_on_non_ai_reply: bool
    customer_visible_fallback_enabled: bool
    stream_enabled: bool
    stream_rollout_percent: int
    stream_require_accept: bool
    trusted_proxy_cidrs: tuple[str, ...]
    rate_limit_trust_x_forwarded_for: bool
    app_env: str

    codex_auth_token_file: str | None
    codex_auth_token: str | None
    codex_app_server_bridge_url: str | None
    codex_app_server_token_file: str | None
    codex_app_server_token_value: str | None
    codex_app_server_timeout_ms: int
    codex_app_server_canary_percent: int
    codex_app_server_kill_switch: bool
    openai_api_key_file: str | None
    openai_api_key: str | None
    tracking_dedupe_scope: str = "tenant_channel_customer"

    @property
    def codex_token(self) -> str | None:
        file_token = _read_secret_file(self.codex_auth_token_file)
        if file_token:
            return file_token
        if self.app_env in {"development", "test", "local"}:
            return _normalize_secret_value(self.codex_auth_token)
        return None

    @property
    def is_codex_configured(self) -> bool:
        return bool(self.codex_enabled and self.codex_token)

    @property
    def codex_app_server_token(self) -> str | None:
        file_token = _read_secret_file(self.codex_app_server_token_file)
        if file_token:
            return file_token
        if self.app_env in {"development", "test", "local"}:
            return _normalize_secret_value(self.codex_app_server_token_value)
        return None

    @property
    def is_codex_app_server_configured(self) -> bool:
        return bool(self.codex_app_server_enabled and self.codex_app_server_bridge_url and self.codex_app_server_token)

    @property
    def openai_token(self) -> str | None:
        file_token = _read_secret_file(self.openai_api_key_file)
        if file_token:
            return file_token
        if self.app_env in {"development", "test", "local"}:
            return _normalize_secret_value(self.openai_api_key)
        return None

    @property
    def is_openai_configured(self) -> bool:
        return bool(self.openai_enabled and self.openai_token)

    def validate_runtime(self) -> None:
        if self.provider not in _ALLOWED_FAST_AI_PROVIDERS:
            raise RuntimeError(
                "WEBCHAT_FAST_AI_PROVIDER must be one of: "
                + ", ".join(sorted(_ALLOWED_FAST_AI_PROVIDERS))
            )
        if self.fallback_provider not in _ALLOWED_FAST_AI_FALLBACK_PROVIDERS:
            raise RuntimeError("WEBCHAT_FAST_AI_FALLBACK_PROVIDER must be openai_responses, rule_engine, or none")
        if self.tracking_dedupe_scope not in _ALLOWED_TRACKING_DEDUPE_SCOPES:
            raise RuntimeError(
                "WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE must be one of: "
                + ", ".join(sorted(_ALLOWED_TRACKING_DEDUPE_SCOPES))
            )
        if self.enabled and self.app_env == "production" and self.provider != _PRODUCTION_FAST_AI_PROVIDER:
            raise RuntimeError(
                "Production WebChat Fast Reply requires WEBCHAT_FAST_AI_PROVIDER=provider_runtime; "
                "legacy direct providers are forbidden in production: "
                + ", ".join(sorted(_PRODUCTION_FORBIDDEN_DIRECT_PROVIDERS))
            )
        if self.provider == "codex_auth" and not self.codex_enabled:
            raise RuntimeError("WEBCHAT_FAST_AI_CODEX_ENABLED=true is required for WEBCHAT_FAST_AI_PROVIDER=codex_auth")
        if self.provider == "codex_app_server" and not self.codex_app_server_enabled:
            raise RuntimeError("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true is required for WEBCHAT_FAST_AI_PROVIDER=codex_app_server")
        if self.provider == "openai_responses" and not self.openai_enabled:
            raise RuntimeError("WEBCHAT_FAST_AI_OPENAI_ENABLED=true is required for WEBCHAT_FAST_AI_PROVIDER=openai_responses")
        if self.timeout_ms < 500 or self.timeout_ms > self.max_timeout_ms:
            raise RuntimeError("WEBCHAT_FAST_AI_TIMEOUT_MS must be between 500 and WEBCHAT_FAST_AI_MAX_TIMEOUT_MS")
        if self.max_timeout_ms > 30000:
            raise RuntimeError("WEBCHAT_FAST_AI_MAX_TIMEOUT_MS must not exceed 30000")
        if self.codex_app_server_timeout_ms < 500 or self.codex_app_server_timeout_ms > self.max_timeout_ms:
            raise RuntimeError("CODEX_APP_SERVER_TIMEOUT_MS must be between 500 and WEBCHAT_FAST_AI_MAX_TIMEOUT_MS")
        if self.codex_app_server_canary_percent < 0 or self.codex_app_server_canary_percent > 100:
            raise RuntimeError("CODEX_APP_SERVER_CANARY_PERCENT must be between 0 and 100")
        if self.stream_rollout_percent < 0 or self.stream_rollout_percent > 100:
            raise RuntimeError("WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT must be between 0 and 100")
        for cidr in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise RuntimeError(f"Invalid TRUSTED_PROXY_CIDRS entry: {cidr}") from exc
        if not self.enabled:
            return

        if self.app_env == "production":
            if self.codex_auth_token:
                raise RuntimeError("CODEX_AUTH_TOKEN is forbidden in production; use CODEX_AUTH_TOKEN_FILE")
            if self.codex_app_server_token_value:
                raise RuntimeError("CODEX_APP_SERVER_TOKEN is forbidden in production; use CODEX_APP_SERVER_TOKEN_FILE")
            if self.openai_api_key and (self.provider == "openai_responses" or self.openai_enabled):
                raise RuntimeError("OPENAI_API_KEY is forbidden in production for this phase; use OPENAI_API_KEY_FILE")
            if self.provider == "codex_auth" and not self.codex_auth_token_file:
                raise RuntimeError("CODEX_AUTH_TOKEN_FILE is required in production when provider=codex_auth")
            if self.provider == "codex_app_server":
                if not self.codex_app_server_token_file:
                    raise RuntimeError("CODEX_APP_SERVER_TOKEN_FILE is required in production when provider=codex_app_server")
                if not self.codex_app_server_bridge_url:
                    raise RuntimeError("CODEX_APP_SERVER_BRIDGE_URL is required in production when provider=codex_app_server")
                _validate_private_runtime_url(self.codex_app_server_bridge_url, setting_name="CODEX_APP_SERVER_BRIDGE_URL")
            if self.provider == "provider_runtime" and self.codex_app_server_bridge_url:
                _validate_private_runtime_url(self.codex_app_server_bridge_url, setting_name="CODEX_APP_SERVER_BRIDGE_URL")
            if self.provider == "openai_responses" and not self.openai_api_key_file:
                raise RuntimeError("OPENAI_API_KEY_FILE is required in production when provider=openai_responses")


def _validate_private_runtime_url(value: str, *, setting_name: str) -> None:
    parsed = urlparse(value or "")

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"{setting_name} must be a valid http(s) URL")

    host = parsed.hostname

    try:
        resolved_ips = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
            resolved_ips = {ipaddress.ip_address(item[4][0]) for item in infos}
        except Exception as exc:
            raise RuntimeError(f"{setting_name} host could not be resolved in production") from exc

    tailnet_or_cgnat = ipaddress.ip_network("100.64.0.0/10")

    def allowed(ip) -> bool:
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip in tailnet_or_cgnat

    if not any(allowed(ip) for ip in resolved_ips):
        raise RuntimeError(f"{setting_name} must point to a private or tailnet host in production")


@lru_cache(maxsize=1)
def get_webchat_fast_settings() -> WebchatFastSettings:
    max_timeout_ms = _env_int("WEBCHAT_FAST_AI_MAX_TIMEOUT_MS", 30000, minimum=500, maximum=30000)
    settings = WebchatFastSettings(
        enabled=_env_bool("WEBCHAT_FAST_AI_ENABLED", True),
        provider=os.getenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime").strip().lower() or "provider_runtime",
        fallback_provider=os.getenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "rule_engine").strip().lower() or "rule_engine",
        codex_enabled=_env_bool("WEBCHAT_FAST_AI_CODEX_ENABLED", False),
        codex_app_server_enabled=_env_bool("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", False),
        openai_enabled=_env_bool("WEBCHAT_FAST_AI_OPENAI_ENABLED", False),
        timeout_ms=_env_int("WEBCHAT_FAST_AI_TIMEOUT_MS", 3000, minimum=500, maximum=max_timeout_ms),
        max_timeout_ms=max_timeout_ms,
        history_turns=_env_int("WEBCHAT_FAST_AI_HISTORY_TURNS", 5, minimum=1, maximum=5),
        max_prompt_chars=_env_int("WEBCHAT_FAST_AI_MAX_PROMPT_CHARS", 2500, minimum=500, maximum=4000),
        rate_limit_window_seconds=_env_int("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=10, maximum=3600),
        rate_limit_max_requests=_env_int("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", 30, minimum=1, maximum=300),
        tracking_dedupe_scope=os.getenv("WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE", "tenant_channel_customer").strip().lower() or "tenant_channel_customer",
        hard_fail_on_non_ai_reply=_env_bool("WEBCHAT_FAST_HARD_FAIL_ON_NON_AI_REPLY", True),
        customer_visible_fallback_enabled=_env_bool("WEBCHAT_FAST_CUSTOMER_VISIBLE_FALLBACK_ENABLED", True),
        stream_enabled=_env_bool("WEBCHAT_FAST_STREAM_ENABLED", False),
        stream_rollout_percent=_env_int("WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT", 0, minimum=0, maximum=100),
        stream_require_accept=_env_bool("WEBCHAT_FAST_STREAM_REQUIRE_ACCEPT", True),
        trusted_proxy_cidrs=_csv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,172.16.0.0/12"),
        rate_limit_trust_x_forwarded_for=_env_bool("WEBCHAT_RATE_LIMIT_TRUST_X_FORWARDED_FOR", True),
        app_env=os.getenv("APP_ENV", "development").strip().lower() or "development",
        codex_auth_token_file=os.getenv("CODEX_AUTH_TOKEN_FILE", "").strip() or None,
        codex_auth_token=os.getenv("CODEX_AUTH_TOKEN", "").strip() or None,
        codex_app_server_bridge_url=os.getenv("CODEX_APP_SERVER_BRIDGE_URL", "").strip() or None,
        codex_app_server_token_file=(os.getenv("CODEX_APP_SERVER_TOKEN_FILE") or os.getenv("CODEX_REPLY_BRIDGE_TOKEN_FILE") or "").strip() or None,
        codex_app_server_token_value=(os.getenv("CODEX_APP_SERVER_TOKEN") or os.getenv("CODEX_REPLY_BRIDGE_TOKEN") or "").strip() or None,
        codex_app_server_timeout_ms=_env_int("CODEX_APP_SERVER_TIMEOUT_MS", 15000, minimum=500, maximum=max_timeout_ms),
        codex_app_server_canary_percent=_env_int("CODEX_APP_SERVER_CANARY_PERCENT", 0, minimum=0),
        codex_app_server_kill_switch=_env_bool("CODEX_APP_SERVER_KILL_SWITCH", False),
        openai_api_key_file=os.getenv("OPENAI_API_KEY_FILE", "").strip() or None,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
    )

    settings.validate_runtime()
    return settings
