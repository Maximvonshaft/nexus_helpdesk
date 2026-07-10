from . import runtime as runtime
from .runtime import (
    KnowledgeRuntimeHit,
    KnowledgeRuntimeOptions,
    KnowledgeRuntimeResult,
)
from .relevance_guard import install as _install_relevance_guard

_install_relevance_guard()

from .data_safety_guard import install as _install_data_safety_guard  # noqa: E402

_install_data_safety_guard()
retrieve_knowledge = runtime.retrieve_knowledge

__all__ = [
    "KnowledgeRuntimeHit",
    "KnowledgeRuntimeOptions",
    "KnowledgeRuntimeResult",
    "retrieve_knowledge",
]
