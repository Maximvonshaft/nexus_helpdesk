from __future__ import annotations

from ..services.background_job_scope import install_background_job_scope_events
from ..services.processing_purpose_enforcement import (
    install_processing_purpose_events,
)
from ..services.read_model_contracts import install_read_model_contracts
from ..services.whatsapp_media_events import install_whatsapp_media_events

_INSTALLED = False


def register_runtime_contracts() -> None:
    """Install process-wide persistence, privacy, read and media guards once."""

    global _INSTALLED
    if _INSTALLED:
        return
    install_background_job_scope_events()
    install_processing_purpose_events()
    install_whatsapp_media_events()
    install_read_model_contracts()
    _INSTALLED = True
