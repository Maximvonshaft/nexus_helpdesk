"""WebChat AI decision runtime.

This package is the policy/tool boundary for public WebChat replies.  The AI may
propose customer text and next actions; the backend validates evidence, tool
contracts, idempotency, redaction, and audit before anything is executed.
"""

from .schemas import AIDecision, AIDecisionToolCall
from .service import build_ai_decision_trace, decision_from_provider_result

__all__ = ["AIDecision", "AIDecisionToolCall", "build_ai_decision_trace", "decision_from_provider_result"]
