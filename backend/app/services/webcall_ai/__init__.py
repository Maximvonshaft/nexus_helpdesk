"""WebCall AI foundation contracts.

PR-0/PR-2 adds guarded config, schemas, persistence, and worker claim
lifecycle only. It does not start an AI voice agent or connect STT/TTS
providers.
"""

from .config import WebCallAISettings, get_webcall_ai_settings
from .lifecycle import (
    WEBCALL_AI_STATUS_CLAIMED,
    WEBCALL_AI_STATUS_FAILED,
    WEBCALL_AI_STATUS_PENDING,
    WEBCALL_AI_STATUS_RELEASED,
    WEBCALL_AI_STATUS_SKIPPED,
    claim_webcall_ai_sessions,
    fail_webcall_ai_session,
    heartbeat_webcall_ai_session,
    release_webcall_ai_session,
)
from .mock_turn_executor import execute_mock_turn_for_claimed_session
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
    "WEBCALL_AI_STATUS_CLAIMED",
    "WEBCALL_AI_STATUS_FAILED",
    "WEBCALL_AI_STATUS_PENDING",
    "WEBCALL_AI_STATUS_RELEASED",
    "WEBCALL_AI_STATUS_SKIPPED",
    "claim_webcall_ai_sessions",
    "fail_webcall_ai_session",
    "execute_mock_turn_for_claimed_session",
    "get_webcall_ai_settings",
    "heartbeat_webcall_ai_session",
    "release_webcall_ai_session",
    "reject_forbidden_action",
]
