from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


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


@dataclass(frozen=True)
class WebchatFastSettings:
    enabled: bool
    provider: str
    timeout_ms: int
    max_timeout_ms: int
    history_turns: int
    max_prompt_chars: int
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    hard_fail_on_non_ai_reply: bool
    stream_enabled: bool
    stream_rollout_percent: int
    stream_require_accept: bool
    trusted_proxy_cidrs: tuple[str, ...]
    rate_limit_trust_x_forwarded_for: bool
    openclaw_responses_url: str
    openclaw_responses_agent_id: str
    openclaw_responses_token_file: str | None
    openclaw_responses_token: str | None
    openclaw_connect_timeout_ms: int
    openclaw_read_timeout_ms: int
    openclaw_total_timeout_ms: int
    openclaw_pool_max_connections: int
    openclaw_pool_max_keepalive: int
    app_env: str

    openclaw_responses_stream_url: str | None
    openclaw_responses_stream_token_file: str | None
    openclaw_responses_stream_token: str | None
    openclaw_stream_connect_timeout_ms: int
    openclaw_stream_read_timeout_ms: int
    openclaw_stream_total_timeout_ms: int

    @property
    def stream_token(self) -> str | None:
        if self.openclaw_responses_stream_token_file:
            path = Path(self.openclaw_responses_stream_token_file)
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                value = ""
            if value.lower().startswith("bearer "):
                value = value.split(None, 1)[1].strip()
            if value:
                return value
        if self.app_env in {"development", "test", "local"}:
            value = (self.openclaw_responses_stream_token or "").strip()
            if value.lower().startswith("bearer "):
                value = value.split(None, 1)[1].strip()
            return value or None
        return None

    @property
    def is_openclaw_stream_configured(self) -> bool:
        return bool(self.openclaw_responses_stream_url and self.stream_token)

    @property
    def token(self) -> str | None:
        if self.openclaw_responses_token_file:
            path = Path(self.openclaw_responses_token_file)
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                value = ""
            if value.lower().startswith("bearer "):
                value = value.split(None, 1)[1].strip()
            if value:
                return value
        if self.app_env in {"development", "test", "local"}:
            value = (self.openclaw_responses_token or "").strip()
            if value.lower().startswith("bearer "):
                value = value.split(None, 1)[1].strip()
            return value or None
        return None

    @property
    def is_openclaw_configured(self) -> bool:
        return bool(self.openclaw_responses_url and self.token)

    def validate_runtime(self) -> None:
        if self.provider != "openclaw_responses":
            raise RuntimeError("WEBCHAT_FAST_AI_PROVIDER must be openclaw_responses")
        if self.timeout_ms < 500 or self.timeout_ms > self.max_timeout_ms:
            raise RuntimeError("WEBCHAT_FAST_AI_TIMEOUT_MS must be between 500 and WEBCHAT_FAST_AI_MAX_TIMEOUT_MS")
        if self.max_timeout_ms > 30000:
            raise RuntimeError("WEBCHAT_FAST_AI_MAX_TIMEOUT_MS must not exceed 30000")
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
            if self.openclaw_responses_token:
                raise RuntimeError("OPENCLAW_RESPONSES_TOKEN is forbidden in production; use OPENCLAW_RESPONSES_TOKEN_FILE")
            if not self.openclaw_responses_token_file:
                raise RuntimeError("OPENCLAW_RESPONSES_TOKEN_FILE is required in production")
            _validate_private_responses_url(self.openclaw_responses_url)
            
            if self.stream_enabled:
                if not self.openclaw_responses_stream_url:
                    raise RuntimeError("OPENCLAW_RESPONSES_STREAM_URL is required in production when stream is enabled")
                if self.openclaw_responses_stream_token:
                    raise RuntimeError("OPENCLAW_RESPONSES_STREAM_TOKEN is forbidden in production; use OPENCLAW_RESPONSES_STREAM_TOKEN_FILE")
                if not self.openclaw_responses_stream_token_file:
                    raise RuntimeError("OPENCLAW_RESPONSES_STREAM_TOKEN_FILE is required in production when stream is enabled")
                _validate_private_responses_url(self.openclaw_responses_stream_url)



def _validate_private_responses_url(value: str) -> None:
    parsed = urlparse(value or "")

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("OPENCLAW_RESPONSES_URL must be a valid http(s) URL")

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
            raise RuntimeError("OPENCLAW_RESPONSES_URL host could not be resolved in production") from exc

    tailnet_or_cgnat = ipaddress.ip_network("100.64.0.0/10")

    def allowed(ip) -> bool:
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip in tailnet_or_cgnat

    if not any(allowed(ip) for ip in resolved_ips):
        raise RuntimeError("OPENCLAW_RESPONSES_URL must point to a private or tailnet host in production")


@lru_cache(maxsize=1)
def get_webchat_fast_settings() -> WebchatFastSettings:
    max_timeout_ms = _env_int("WEBCHAT_FAST_AI_MAX_TIMEOUT_MS", 30000, minimum=500, maximum=30000)
    settings = WebchatFastSettings(
        enabled=_env_bool("WEBCHAT_FAST_AI_ENABLED", True),
        provider=os.getenv("WEBCHAT_FAST_AI_PROVIDER", "openclaw_responses").strip().lower() or "openclaw_responses",
        timeout_ms=_env_int("WEBCHAT_FAST_AI_TIMEOUT_MS", 3000, minimum=500, maximum=max_timeout_ms),
        max_timeout_ms=max_timeout_ms,
        history_turns=_env_int("WEBCHAT_FAST_AI_HISTORY_TURNS", 5, minimum=1, maximum=5),
        max_prompt_chars=_env_int("WEBCHAT_FAST_AI_MAX_PROMPT_CHARS", 2500, minimum=500, maximum=4000),
        rate_limit_window_seconds=_env_int("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=10, maximum=3600),
        rate_limit_max_requests=_env_int("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", 30, minimum=1, maximum=300),
        hard_fail_on_non_ai_reply=_env_bool("WEBCHAT_FAST_HARD_FAIL_ON_NON_AI_REPLY", True),
        stream_enabled=_env_bool("WEBCHAT_FAST_STREAM_ENABLED", False),
        stream_rollout_percent=_env_int("WEBCHAT_FAST_STREAM_ROLLOUT_PERCENT", 0, minimum=0, maximum=100),
        stream_require_accept=_env_bool("WEBCHAT_FAST_STREAM_REQUIRE_ACCEPT", True),
        trusted_proxy_cidrs=_csv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,172.16.0.0/12"),
        rate_limit_trust_x_forwarded_for=_env_bool("WEBCHAT_RATE_LIMIT_TRUST_X_FORWARDED_FOR", True),
        openclaw_responses_url=os.getenv("OPENCLAW_RESPONSES_URL", "").strip(),
        openclaw_responses_agent_id=os.getenv("OPENCLAW_RESPONSES_AGENT_ID", "webchat-fast").strip() or "webchat-fast",
        openclaw_responses_token_file=os.getenv("OPENCLAW_RESPONSES_TOKEN_FILE"),
        openclaw_responses_token=os.getenv("OPENCLAW_RESPONSES_TOKEN"),
        openclaw_connect_timeout_ms=_env_int("OPENCLAW_RESPONSES_CONNECT_TIMEOUT_MS", 500, minimum=100, maximum=3000),
        openclaw_read_timeout_ms=_env_int("OPENCLAW_RESPONSES_READ_TIMEOUT_MS", 3000, minimum=500, maximum=max_timeout_ms),
        openclaw_total_timeout_ms=_env_int("OPENCLAW_RESPONSES_TOTAL_TIMEOUT_MS", 30000, minimum=1000, maximum=30000),
        openclaw_pool_max_connections=_env_int("OPENCLAW_RESPONSES_POOL_MAX_CONNECTIONS", 10, minimum=1, maximum=50),
        openclaw_pool_max_keepalive=_env_int("OPENCLAW_RESPONSES_POOL_MAX_KEEPALIVE", 5, minimum=0, maximum=25),
        app_env=os.getenv("APP_ENV", "development").strip().lower() or "development",
        openclaw_responses_stream_url=os.getenv("OPENCLAW_RESPONSES_STREAM_URL", "").strip() or None,
        openclaw_responses_stream_token_file=os.getenv("OPENCLAW_RESPONSES_STREAM_TOKEN_FILE", "").strip() or None,
        openclaw_responses_stream_token=os.getenv("OPENCLAW_RESPONSES_STREAM_TOKEN", "").strip() or None,
        openclaw_stream_connect_timeout_ms=_env_int("OPENCLAW_RESPONSES_STREAM_CONNECT_TIMEOUT_MS", 500, minimum=100, maximum=3000),
        openclaw_stream_read_timeout_ms=_env_int("OPENCLAW_RESPONSES_STREAM_READ_TIMEOUT_MS", 15000, minimum=500, maximum=30000),
        openclaw_stream_total_timeout_ms=_env_int("OPENCLAW_RESPONSES_STREAM_TOTAL_TIMEOUT_MS", 30000, minimum=1000, maximum=60000),
    )

    settings.validate_runtime()
    return settings
