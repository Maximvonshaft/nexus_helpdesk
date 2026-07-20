"""Public governed tool-execution authority.

The private core owns the single executor, handler registry, policy, audit and
idempotency implementation. This module is only the stable public import path.
"""

from __future__ import annotations

from . import tool_execution_service_core as _core
from .tool_execution_service_core import *  # noqa: F401,F403

_production_handlers = _core._production_handlers
_availability_customer_summary = _core._availability_customer_summary


def __getattr__(name: str):
    return getattr(_core, name)
