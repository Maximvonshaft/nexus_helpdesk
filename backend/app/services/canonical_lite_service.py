"""Canonical public facade for the retained Lite case state machine.

The implementation remains private to this facade.  Ticket operations are resolved
through the canonical Ticket service by ``lite_service``; this module must never
mutate another module at import time.
"""

from . import lite_service as _core
from .lite_service import (
    LITE_STATUS_ORDER,
    assign_lite_case,
    change_lite_status,
    create_lite_case,
    get_lite_case,
    list_lite_cases,
    save_ai_intake_lite,
    save_human_note_lite,
    update_lite_case,
    workflow_update_lite_case,
)


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "LITE_STATUS_ORDER",
    "assign_lite_case",
    "change_lite_status",
    "create_lite_case",
    "get_lite_case",
    "list_lite_cases",
    "save_ai_intake_lite",
    "save_human_note_lite",
    "update_lite_case",
    "workflow_update_lite_case",
]
