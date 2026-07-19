"""WebChat handoff public authority.

The private core owns capability-derived visibility and handoff transitions.
This module performs no import-time mutation and has no compatibility fallback.
"""

from . import webchat_handoff_service_core as _core
from .webchat_handoff_service_core import (
    accept_handoff_request,
    decline_handoff_request,
    ensure_can_reply_in_handoff,
    force_takeover_ticket,
    list_handoff_queue,
    release_handoff_request,
    request_webchat_handoff,
    resume_ai_for_handoff,
    serialize_handoff_request,
)


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "accept_handoff_request",
    "decline_handoff_request",
    "ensure_can_reply_in_handoff",
    "force_takeover_ticket",
    "list_handoff_queue",
    "release_handoff_request",
    "request_webchat_handoff",
    "resume_ai_for_handoff",
    "serialize_handoff_request",
]
