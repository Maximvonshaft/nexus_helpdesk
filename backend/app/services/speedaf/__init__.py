"""Speedaf customer-service adapter package.

This package is intentionally backend-only. Frontend clients and LLM providers
must not call Speedaf APIs directly.
"""

from .client import SpeedafMcpClient, SpeedafMcpClientError, SpeedafMcpResponse
from .adapter import SpeedafCoreAdapter
from .track_query import SpeedafTrackQueryClient, SpeedafTrackQueryError

__all__ = [
    "SpeedafMcpClient",
    "SpeedafMcpClientError",
    "SpeedafMcpResponse",
    "SpeedafCoreAdapter",
    "SpeedafTrackQueryClient",
    "SpeedafTrackQueryError",
]
