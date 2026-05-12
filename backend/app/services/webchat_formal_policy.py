from __future__ import annotations

import os

from ..enums import TicketStatus
from ..models import Ticket


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def webchat_frontline_ai_enabled() -> bool:
    """Whether Webchat can provide customer-visible AI frontline service replies."""
    return _env_bool("WEBCHAT_FRONTLINE_AI_ENABLED", True)


def webchat_formal_outbound_enabled() -> bool:
    """Whether Webchat may carry final/formal resolution notifications.

    The default is false: formal resolution outbound must go through Email or
    WhatsApp as draft -> human approval -> provider dispatch.
    """
    return _env_bool("WEBCHAT_FORMAL_OUTBOUND_ENABLED", False)


def webchat_public_config() -> dict[str, bool]:
    return {
        "frontline_ai_enabled": webchat_frontline_ai_enabled(),
        "formal_outbound_disabled": not webchat_formal_outbound_enabled(),
    }


def is_formal_resolution_context(ticket: Ticket | None, source: str | None = None) -> bool:
    if ticket is None:
        return False
    source_value = (source or "").strip().lower()
    return (
        ticket.status in {TicketStatus.resolved, TicketStatus.closed}
        or bool((ticket.resolution_summary or "").strip())
        or bool((ticket.customer_update or "").strip())
        or source_value in {"human_resolution_note", "auto_reply_from_resolution"}
    )
