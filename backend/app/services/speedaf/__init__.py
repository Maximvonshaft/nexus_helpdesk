"""Speedaf customer-service adapter package.

This package is intentionally backend-only. Frontend clients and LLM providers
must not call Speedaf APIs directly.
"""

from .client import SpeedafMcpClient, SpeedafMcpClientError, SpeedafMcpResponse
from .adapter import SpeedafCoreAdapter
from .track_query import SpeedafTrackQueryClient, SpeedafTrackQueryError

# Legacy callers import the hybrid helpers from ``tracking_fact_source``.
# Bind those public entry points to the same contract-safe implementation used
# by the canonical tracking service so history can never become current truth.
from . import tracking_fact_source as _tracking_fact_source  # noqa: E402
from .tracking_truth_source import (  # noqa: E402
    lookup_speedaf_contract_safe_hybrid_tracking_fact as _safe_hybrid_lookup,
    merge_contract_safe_hybrid_tracking_fact as _safe_hybrid_merge,
)

_tracking_fact_source.lookup_speedaf_hybrid_tracking_fact = _safe_hybrid_lookup
_tracking_fact_source.merge_speedaf_hybrid_tracking_fact = _safe_hybrid_merge

__all__ = [
    "SpeedafMcpClient",
    "SpeedafMcpClientError",
    "SpeedafMcpResponse",
    "SpeedafCoreAdapter",
    "SpeedafTrackQueryClient",
    "SpeedafTrackQueryError",
]
