"""Canonical integration API router.

Actor selection and authorization are implemented directly by ``integration_core``
through effective capabilities. This facade exposes the router without mutating
another module at import time.
"""

from . import integration_core as _core

router = _core.router


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = ["router"]
