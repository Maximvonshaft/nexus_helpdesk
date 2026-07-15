from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal

PolicyLevel = Literal["allow", "block"]

MAX_CUSTOMER_VISIBLE_CHARS = 4000

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_ASSIGNED_SECRET_RE = re.compile(
    r"\b(?:api[_ -]?key|secret[_ -]?key|access[_ -]?token|client[_ -]?secret|password|passwd)"
    r"\s*[:=]\s*[^\s,;]{8,}",
    re.IGNORECASE,
)
_INTERNAL_REASONING_MARKERS = (
    "<think",
    "</think",
    "chain of thought",
    "hidden reasoning",
    "developer instruction",
    "developer message",
    "system prompt",
)


@dataclass(frozen=True)
class CustomerVisiblePolicyDecision:
    allowed: bool
    level: PolicyLevel
    reasons: list[str] = field(default_factory=list)
    normalized_body: str = ""


def evaluate_customer_visible_policy(body: str | None) -> CustomerVisiblePolicyDecision:
    """Validate transport-safe customer text without inferring business meaning.

    Business truth, grounding, origin, and post-signature integrity belong to the
    signed AI reply contract. This policy only blocks malformed content or direct
    disclosure of credentials/internal reasoning.
    """

    original_body = body if isinstance(body, str) else ""
    if not original_body.strip():
        return CustomerVisiblePolicyDecision(False, "block", ["empty_customer_visible_body"], original_body)
    if len(original_body) > MAX_CUSTOMER_VISIBLE_CHARS:
        return CustomerVisiblePolicyDecision(False, "block", ["customer_visible_body_too_long"], original_body)

    lowered = original_body.lower()
    if any(marker in lowered for marker in _INTERNAL_REASONING_MARKERS):
        return CustomerVisiblePolicyDecision(False, "block", ["internal_reasoning_disclosure"], original_body)
    if _PRIVATE_KEY_RE.search(original_body):
        return CustomerVisiblePolicyDecision(False, "block", ["private_key_disclosure"], original_body)
    if _BEARER_TOKEN_RE.search(original_body):
        return CustomerVisiblePolicyDecision(False, "block", ["bearer_token_disclosure"], original_body)
    if _GITHUB_TOKEN_RE.search(original_body):
        return CustomerVisiblePolicyDecision(False, "block", ["repository_token_disclosure"], original_body)
    if _JWT_RE.search(original_body):
        return CustomerVisiblePolicyDecision(False, "block", ["jwt_disclosure"], original_body)
    if _ASSIGNED_SECRET_RE.search(original_body):
        return CustomerVisiblePolicyDecision(False, "block", ["assigned_secret_disclosure"], original_body)

    return CustomerVisiblePolicyDecision(True, "allow", [], original_body)


def format_policy_reasons(decision: CustomerVisiblePolicyDecision) -> str:
    return "; ".join(decision.reasons) if decision.reasons else decision.level
