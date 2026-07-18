"""Canonical unified operator queue authority.

The private implementation owns capability-derived visibility directly. This
facade exposes the queue without mutating implementation globals.
"""

from . import operator_work_queue_core as _core
from .operator_work_queue_core import list_unified_operator_queue


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = ["list_unified_operator_queue"]
