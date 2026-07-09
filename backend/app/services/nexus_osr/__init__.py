"""Nexus Operations Service Runtime foundation.

This package contains product-level runtime contracts and policy primitives for
Nexus OSR.  The modules are intentionally framework-light so WebChat, WhatsApp,
operator workbench, and future channels can share the same decision contracts.
"""

from .runtime_decision_contract import (
    BusinessReplyType,
    EvidenceSource,
    EvidenceType,
    RuntimeDecision,
    RuntimeDecisionEvaluation,
    RuntimeDecisionViolation,
    evaluate_runtime_decision,
)
from .case_context import CaseContext, CaseContextStatus
from .policies import (
    EscalationDecision,
    EscalationPolicy,
    HumanAvailabilityDecision,
    HumanHoursPolicy,
    ToolExecutionPolicy,
    ToolPolicyDecision,
)

__all__ = [
    "BusinessReplyType",
    "CaseContext",
    "CaseContextStatus",
    "EscalationDecision",
    "EscalationPolicy",
    "EvidenceSource",
    "EvidenceType",
    "HumanAvailabilityDecision",
    "HumanHoursPolicy",
    "RuntimeDecision",
    "RuntimeDecisionEvaluation",
    "RuntimeDecisionViolation",
    "ToolExecutionPolicy",
    "ToolPolicyDecision",
    "evaluate_runtime_decision",
]
