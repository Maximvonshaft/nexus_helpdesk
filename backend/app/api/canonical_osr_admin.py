"""Canonical OSR administration router.

The private implementation is capability-native. This facade exposes it without
role-name authorization or import-time mutation.
"""

from . import osr_admin_core as _core

router = _core.router


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = ["router"]
