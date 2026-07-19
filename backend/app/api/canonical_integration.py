"""Canonical integration API router.

Actor selection, authorization and idempotency are implemented by the single
private ``integration_runtime`` module through effective capabilities. This
public authority exposes the router without a compatibility fallback.
"""

from . import integration_runtime as _runtime

router = _runtime.router


def __getattr__(name: str):
    return getattr(_runtime, name)


__all__ = ["router"]
