from __future__ import annotations

from typing import Any

from .answer_planner import plan_answer
from .flags import DomainRuntimeFlags
from .query_understanding import understand_query


def build_webchat_domain_shadow_trace(
    *,
    body: str,
    tenant_key: str,
    channel_key: str,
    market_id: int | None = None,
    language: str | None = None,
    flags: DomainRuntimeFlags | None = None,
) -> dict[str, Any] | None:
    """Build a non-enforcing domain trace for WebChat runtime context.

    This bridge is intentionally side-effect free. It does not call tools, create
    tickets, trigger handoff, alter retrieval, or change customer replies.
    """
    flags = flags or DomainRuntimeFlags.from_env()
    if not flags.trace_enabled or not flags.webchat_shadow_trace_enabled:
        return None

    understanding = understand_query(body, shadow_mode=True)
    plan = plan_answer(understanding)
    return {
        "trace_version": "domain_webchat_shadow_trace_v1",
        "enabled": flags.enabled,
        "shadow_mode": True,
        "enforced": False,
        "tenant_key": tenant_key,
        "channel_key": channel_key,
        "market_id": market_id,
        "language": language,
        "understanding": understanding.as_trace(),
        "answer_plan": plan.as_trace(),
        "side_effects": {
            "tool_executed": False,
            "ticket_created": False,
            "handoff_triggered": False,
            "reply_changed": False,
            "retrieval_changed": False,
        },
    }
