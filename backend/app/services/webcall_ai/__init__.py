"""WebCall AI foundation contracts.

This package exposes guarded config, schemas, persistence helpers, worker
claim lifecycle, deterministic mock turn execution, and mock media boundary
contracts. It does not start a functional AI voice agent.
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
from .media_schemas import MockSTTInput, MockSTTResult, MockTTSInput, MockTTSResult
from .mock_media_provider import MockSTTProvider, MockTTSProvider
from .mock_turn_executor import MockTurnExecutionResult, execute_mock_turn_for_claimed_session
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
    "MockSTTInput",
    "MockSTTProvider",
    "MockSTTResult",
    "MockTTSInput",
    "MockTTSProvider",
    "MockTTSResult",
    "MockTurnExecutionResult",
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
