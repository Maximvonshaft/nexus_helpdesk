"""WebCall AI foundation contracts.

PR-0/PR-1 adds guarded config, schemas, and persistence only. It does not
start an AI voice agent or connect STT/TTS providers.
"""

from .config import WebCallAISettings, get_webcall_ai_settings
from .schemas import (
    WebCallAIActionDecision,
    WebCallAIAllowedAction,
    WebCallAIForbiddenAction,
    WebCallAITurnDecision,
    reject_forbidden_action,
)

__all__ = [
    "WebCallAIActionDecision",
    "WebCallAIAllowedAction",
    "WebCallAIForbiddenAction",
    "WebCallAISettings",
    "WebCallAITurnDecision",
    "get_webcall_ai_settings",
    "reject_forbidden_action",
]
