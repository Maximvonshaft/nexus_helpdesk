"""Canonical QA training service.

The private implementation owns capability-derived case visibility directly.
This facade contains no business logic and performs no import-time mutation.
"""

from . import qa_training_service_core as _core
from .qa_training_service_core import (
    build_qa_training,
    submit_agent_appeal,
    submit_knowledge_gap,
)


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "build_qa_training",
    "submit_agent_appeal",
    "submit_knowledge_gap",
]
