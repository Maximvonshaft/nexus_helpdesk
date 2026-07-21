"""Public governed Tool-execution authority.

The private core remains the only executor, policy, audit and idempotency
implementation. Agent extensions bind request-local handlers through the
canonical ``ControlledActionExecutor`` and never mutate this module or its
private core.
"""
from __future__ import annotations

from ..agent_tool_contracts import bootstrap_agent_tool_contracts
from . import tool_execution_service_core as _core

bootstrap_agent_tool_contracts()

from .tool_execution_service_core import *  # noqa: E402,F401,F403

_availability_customer_summary = _core._availability_customer_summary


def __getattr__(name: str):
    return getattr(_core, name)
