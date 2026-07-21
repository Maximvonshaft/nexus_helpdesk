"""Public governed Tool-execution authority.

The private core remains the only executor, policy, audit and idempotency
implementation. Configurable Agent extensions register contracts and production
handlers into that authority; they do not create a parallel dispatcher.
"""
from __future__ import annotations

from ..agent_tool_contracts import bootstrap_agent_tool_contracts
from ..agent_tool_handlers import build_agent_tool_handlers, extension_executable_tool_names
from ..knowledge_release_retrieval import retrieve_release_published_chunks
from . import tool_execution_service_core as _core

bootstrap_agent_tool_contracts()
_ORIGINAL_PRODUCTION_HANDLERS = _core._production_handlers
_ORIGINAL_RETRIEVE_PUBLISHED_CHUNKS = _core.retrieve_published_chunks


def _production_handlers(db, *, conversation, ticket, customer):
    handlers = _ORIGINAL_PRODUCTION_HANDLERS(
        db,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
    )
    extensions = build_agent_tool_handlers(
        db,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
    )
    overlap = set(handlers) & set(extensions)
    if overlap:
        raise RuntimeError(f"duplicate canonical Tool handlers: {sorted(overlap)}")
    handlers.update(extensions)
    return handlers


def _release_scoped_retrieve_published_chunks(*args, **kwargs):
    release_result = retrieve_release_published_chunks(*args, **kwargs)
    if release_result is not None:
        return release_result
    return _ORIGINAL_RETRIEVE_PUBLISHED_CHUNKS(*args, **kwargs)


_core._production_handlers = _production_handlers
_core.retrieve_published_chunks = _release_scoped_retrieve_published_chunks
_core._EXECUTABLE_TOOL_NAMES = tuple(
    sorted(set(_core._EXECUTABLE_TOOL_NAMES) | set(extension_executable_tool_names()))
)

from .tool_execution_service_core import *  # noqa: E402,F401,F403

_availability_customer_summary = _core._availability_customer_summary


def __getattr__(name: str):
    return getattr(_core, name)
