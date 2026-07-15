"""Compatibility import for the canonical Control Tower service."""

from . import canonical_control_tower_service as _canonical
from .canonical_control_tower_service import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_canonical, name)
