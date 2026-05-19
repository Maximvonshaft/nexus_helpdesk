"""Speedaf MCP customer-service adapter package.

This package is intentionally backend-only. Frontend clients and LLM providers
must not call Speedaf MCP APIs directly.
"""

from .client import SpeedafMcpClient, SpeedafMcpClientError, SpeedafMcpResponse
from .adapter import SpeedafCoreAdapter

__all__ = [
    "SpeedafMcpClient",
    "SpeedafMcpClientError",
    "SpeedafMcpResponse",
    "SpeedafCoreAdapter",
]
