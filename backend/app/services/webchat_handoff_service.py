"""Compatibility import for the canonical WebChat handoff service."""

from . import canonical_webchat_handoff_service as _canonical
from .canonical_webchat_handoff_service import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_canonical, name)
