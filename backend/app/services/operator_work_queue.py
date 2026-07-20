"""Unified operator work-queue public authority.

The canonical projection includes ticket-backed work and ticketless WebChat
handoffs without introducing a second queue store.
"""

from . import operator_work_queue_core as _core
from .conversation_operator_queue import list_unified_operator_queue
from .operator_work_queue_core import decode_unified_operator_queue_cursor


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "decode_unified_operator_queue_cursor",
    "list_unified_operator_queue",
]
