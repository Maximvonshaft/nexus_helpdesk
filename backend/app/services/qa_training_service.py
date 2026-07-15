"""Compatibility import for the canonical QA training service."""

from . import canonical_qa_training_service as _canonical
from .canonical_qa_training_service import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_canonical, name)
