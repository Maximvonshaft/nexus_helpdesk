"""Compatibility import for the canonical OSR administration router."""

from . import canonical_osr_admin as _canonical

router = _canonical.router


def __getattr__(name: str):
    return getattr(_canonical, name)


__all__ = ["router"]
