"""Compatibility import for the canonical Ticket service."""

from . import canonical_ticket_service as _canonical
from .canonical_ticket_service import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_canonical, name)
