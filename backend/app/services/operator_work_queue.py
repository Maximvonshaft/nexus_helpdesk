"""Unified operator queue public authority.

The private core owns capability-derived visibility. This module exposes the
queue without import-time mutation or a compatibility layer.
"""

from . import operator_work_queue_core as _core
from .operator_work_queue_core import list_unified_operator_queue


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = ["list_unified_operator_queue"]
