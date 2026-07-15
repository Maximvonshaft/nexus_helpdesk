"""Compatibility import for the canonical integration router."""

from . import canonical_integration as _canonical

router = _canonical.router


def __getattr__(name: str):
    return getattr(_canonical, name)


__all__ = ["router"]
