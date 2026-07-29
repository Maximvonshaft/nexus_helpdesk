from __future__ import annotations

from ..services.background_job_scope import install_background_job_scope_events
from ..services.golden_journey_portfolio import (
    install_golden_journey_portfolio_guard,
)
from ..services.handoff_assignment_contract import (
    install_handoff_assignment_contract,
)
from ..services.processing_purpose_enforcement import (
    install_processing_purpose_events,
)
from ..services.read_model_contracts import install_read_model_contracts
from ..services.whatsapp_media_events import install_whatsapp_media_events

_INSTALLED = False


def register_runtime_contracts() -> None:
    """Install process-wide persistence, privacy, product and routing guards once."""

    global _INSTALLED
    if _INSTALLED:
        return
    install_background_job_scope_events()
    install_processing_purpose_events()
    install_whatsapp_media_events()
    install_golden_journey_portfolio_guard()
    install_handoff_assignment_contract()
    install_read_model_contracts()
    _INSTALLED = True
