from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Hashable

from .runtime_capabilities import (
    CapabilityProbeResult,
    RuntimeCapabilityExpectations,
    build_capability_url,
    probe_private_ai_runtime_capabilities,
)

DEFAULT_READY_TTL_SECONDS = 10.0
DEFAULT_NOT_READY_TTL_SECONDS = 1.0
_MAX_READY_TTL_SECONDS = 60.0
_MAX_NOT_READY_TTL_SECONDS = 5.0


class CapabilityProbeCache:
    """Small process-local cache for exact Runtime capability evidence.

    The lock intentionally covers the probe on a cache miss so concurrent
    requests cannot stampede the authenticated Runtime endpoint. The cache key
    includes exact expectations and a token-file stat fingerprint. No token
    contents are retained.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 128,
    ) -> None:
        self._clock = clock
        self._max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[
            Hashable,
            tuple[float, CapabilityProbeResult],
        ] = OrderedDict()
        self._lock = threading.RLock()

    def get_or_probe(
        self,
        *,
        key: Hashable,
        probe: Callable[[], CapabilityProbeResult],
        ready_ttl_seconds: float = DEFAULT_READY_TTL_SECONDS,
        not_ready_ttl_seconds: float = DEFAULT_NOT_READY_TTL_SECONDS,
    ) -> CapabilityProbeResult:
        ready_ttl = _bounded_ttl(
            ready_ttl_seconds,
            maximum=_MAX_READY_TTL_SECONDS,
        )
        not_ready_ttl = _bounded_ttl(
            not_ready_ttl_seconds,
            maximum=_MAX_NOT_READY_TTL_SECONDS,
        )
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None:
                expires_at, result = entry
                if now < expires_at:
                    self._entries.move_to_end(key)
                    return result
                self._entries.pop(key, None)

            result = probe()
            if not isinstance(result, CapabilityProbeResult):
                result = CapabilityProbeResult.not_ready(
                    "capability_payload_malformed"
                )
            ttl = ready_ttl if result.ready else not_ready_ttl
            self._evict_expired(self._clock())
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
            self._entries[key] = (self._clock() + ttl, result)
            return result

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _evict_expired(self, now: float) -> None:
        expired = [
            key
            for key, (expires_at, _result) in self._entries.items()
            if now >= expires_at
        ]
        for key in expired:
            self._entries.pop(key, None)


def build_capability_cache_key(
    *,
    base_url: str,
    capabilities_path: str,
    token_file: str,
    expectations: RuntimeCapabilityExpectations,
) -> tuple[object, ...]:
    try:
        endpoint = build_capability_url(base_url, capabilities_path)
    except Exception:
        endpoint = "invalid"
    try:
        stat = Path(token_file).stat()
        token_fingerprint: tuple[object, ...] = (
            "present",
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
        )
    except OSError:
        token_fingerprint = ("missing",)
    return (
        endpoint,
        str(Path(token_file)) if token_file else "",
        token_fingerprint,
        expectations,
    )


_DEFAULT_CACHE = CapabilityProbeCache()


def probe_private_ai_runtime_capabilities_cached(
    *,
    base_url: str,
    capabilities_path: str,
    token_file: str,
    expectations: RuntimeCapabilityExpectations,
    timeout_seconds: int | float,
    ready_ttl_seconds: float = DEFAULT_READY_TTL_SECONDS,
    not_ready_ttl_seconds: float = DEFAULT_NOT_READY_TTL_SECONDS,
    cache: CapabilityProbeCache = _DEFAULT_CACHE,
) -> CapabilityProbeResult:
    key = build_capability_cache_key(
        base_url=base_url,
        capabilities_path=capabilities_path,
        token_file=token_file,
        expectations=expectations,
    )
    return cache.get_or_probe(
        key=key,
        ready_ttl_seconds=ready_ttl_seconds,
        not_ready_ttl_seconds=not_ready_ttl_seconds,
        probe=lambda: probe_private_ai_runtime_capabilities(
            base_url=base_url,
            capabilities_path=capabilities_path,
            token_file=token_file,
            expectations=expectations,
            timeout_seconds=timeout_seconds,
        ),
    )


def _bounded_ttl(value: float, *, maximum: float) -> float:
    try:
        ttl = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(ttl):
        return 0.0
    return min(max(ttl, 0.0), maximum)
