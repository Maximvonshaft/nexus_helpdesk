from __future__ import annotations

from ..services.background_job_scope import install_background_job_scope_events
from ..services.processing_purpose_enforcement import (
    install_processing_purpose_events,
)

_INSTALLED = False


def register_runtime_contracts() -> None:
    """Install process-wide persistence and privacy guards exactly once."""

    global _INSTALLED
    if _INSTALLED:
        return
    install_background_job_scope_events()
    install_processing_purpose_events()
    _INSTALLED = True
