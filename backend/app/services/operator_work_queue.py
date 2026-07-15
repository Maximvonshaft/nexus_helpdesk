"""Compatibility import for the canonical unified operator queue."""

from . import canonical_operator_work_queue as _canonical
from .canonical_operator_work_queue import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_canonical, name)
