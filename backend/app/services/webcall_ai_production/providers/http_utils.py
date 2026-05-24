from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import httpx

from .base import ProviderError

T = TypeVar("T")


def read_secret_file(path: str | None, *, provider: str) -> str:
    value = (path or "").strip()
    if not value:
        raise ProviderError(provider, "secret_file_required", "provider secret file is required")
    resolved = Path(value)
    if not resolved.is_file():
        raise ProviderError(provider, "secret_file_missing", "provider secret file is missing")
    return resolved.read_text(encoding="utf-8").strip()


def endpoint_required(endpoint: str | None, *, provider: str) -> str:
    value = (endpoint or "").strip()
    if not value:
        raise ProviderError(provider, "endpoint_required", "provider endpoint is required")
    if not (value.startswith("https://") or _local_test_endpoint_allowed(value)):
        raise ProviderError(provider, "endpoint_invalid", "provider endpoint must be https")
    return value


def retry_call(fn: Callable[[], T], *, provider: str, retries: int = 1, retry_delay_seconds: float = 0.2) -> T:
    last_error: ProviderError | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            return fn()
        except ProviderError as exc:
            if exc.code in {"provider_http_429", "provider_http_500", "provider_timeout", "provider_network_error"}:
                last_error = exc
                if attempt < retries:
                    time.sleep(retry_delay_seconds)
                    continue
            raise
    raise last_error or ProviderError(provider, "provider_retry_exhausted")


def classify_http_error(provider: str, exc: Exception) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(provider, "provider_timeout", "provider request timed out")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401 or code == 403:
            return ProviderError(provider, "provider_auth_failed", "provider authentication failed")
        if code == 429:
            return ProviderError(provider, "provider_http_429", "provider rate limited request")
        if code >= 500:
            return ProviderError(provider, "provider_http_500", "provider server error")
        return ProviderError(provider, f"provider_http_{code}", "provider rejected request")
    if isinstance(exc, httpx.HTTPError):
        return ProviderError(provider, "provider_network_error", "provider network error")
    if isinstance(exc, ProviderError):
        return exc
    return ProviderError(provider, "provider_error", type(exc).__name__)


def _local_test_endpoint_allowed(endpoint: str) -> bool:
    if (os.getenv("APP_ENV") or "development").strip().lower() == "production":
        return False
    return endpoint.startswith("http://127.0.0.1") or endpoint.startswith("http://localhost")
