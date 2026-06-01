from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.tracking_fact_schema import hash_tracking_number
from app.services.webchat_fast_output_parser import FastReplyParseError, assert_customer_visible_reply_is_safe

from .schemas import AIDecision
from .tool_registry import ToolContract, get_tool_contract


@dataclass(frozen=True)
class PolicyViolation:
    code: str
    message: str
    tool_name: str | None = None
    risk_level: str | None = None


@dataclass(frozen=True)
class PolicyGateResult:
    ok: bool
    violations: tuple[PolicyViolation, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    checked_tools: tuple[str, ...] = field(default_factory=tuple)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violations": [violation.__dict__ for violation in self.violations],
            "warnings": list(self.warnings),
            "checked_tools": list(self.checked_tools),
        }


_STATUS_CLAIM_RE = re.compile(
    r"\b(parcel|package|shipment|order|waybill)\b[^.!?\n]{0,120}\b("
    r"delivered|in transit|transit|out for delivery|returned|returning|cancelled|canceled|lost|damaged|"
    r"held|customs|cleared|arrived|departed|signed|picked up|delivery failed|failed delivery"
    r")\b",
    re.IGNORECASE,
)
_ZH_STATUS_CLAIM_RE = re.compile(
    r"(包裹|快件|运单|订单)[^。！？\n]{0,60}(已签收|派送中|运输中|退回|已退回|取消|已取消|丢失|破损|清关|到达|离开|派送失败|妥投)",
    re.IGNORECASE,
)
_TRACKING_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9][A-Z0-9._-]{7,47})(?![A-Z0-9])", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_SECRET_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{10,}|[A-Za-z0-9_-]{32,})\b")


def _tracking_fact_evidence_present(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("fact_evidence_present") and metadata.get("pii_redacted"))


def _contains_live_tracking_claim(reply: str) -> bool:
    return bool(_STATUS_CLAIM_RE.search(reply or "") or _ZH_STATUS_CLAIM_RE.search(reply or ""))


def _raw_tracking_exposed(text: str, tracking_number: str | None = None) -> bool:
    if tracking_number and tracking_number.strip() and tracking_number.strip().upper() in (text or "").upper():
        return True
    for match in _TRACKING_RE.finditer(text or ""):
        token = re.sub(r"[^A-Z0-9]", "", match.group(1).upper())
        if len(token) >= 10 and any(ch.isdigit() for ch in token):
            return True
    return False


def _raw_phone_or_secret_exposed(text: str) -> bool:
    return bool(_PHONE_RE.search(text or "") or _SECRET_RE.search(text or ""))


def _tool_names(decision: AIDecision) -> tuple[str, ...]:
    return tuple(call.tool_name for call in decision.tool_calls)


def _handoff_tool_present(decision: AIDecision) -> bool:
    return "handoff.request.create" in _tool_names(decision)


def _tool_contracts(decision: AIDecision) -> tuple[ToolContract, ...]:
    contracts: list[ToolContract] = []
    for call in decision.tool_calls:
        contract = get_tool_contract(call.tool_name)
        if contract is not None:
            contracts.append(contract)
    return tuple(contracts)


def validate_ai_decision(
    decision: AIDecision,
    *,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_number: str | None = None,
    allow_high_risk_write_execution: bool = False,
) -> PolicyGateResult:
    """Validate an AI decision before WebChat executes or returns it.

    The policy gate is intentionally stricter than prompt instructions.  Prompt
    rules can shape behavior; this function is the backend authority.
    """

    violations: list[PolicyViolation] = []
    warnings: list[str] = []
    checked_tools: list[str] = []

    try:
        assert_customer_visible_reply_is_safe(decision.customer_reply)
    except FastReplyParseError as exc:
        violations.append(PolicyViolation(code="unsafe_customer_reply", message=str(exc), risk_level="high"))

    if _raw_tracking_exposed(decision.customer_reply, tracking_number=tracking_number):
        violations.append(
            PolicyViolation(
                code="raw_tracking_exposed",
                message="AI customer reply exposes a raw waybill/tracking number instead of hash/suffix-only reference.",
                risk_level="high",
            )
        )
    if _raw_phone_or_secret_exposed(decision.customer_reply):
        violations.append(
            PolicyViolation(
                code="raw_caller_or_secret_exposed",
                message="AI customer reply exposes a phone/caller ID, token, or secret-like value.",
                risk_level="high",
            )
        )

    evidence_present = _tracking_fact_evidence_present(tracking_fact_metadata)
    if decision.intent == "tracking" and _contains_live_tracking_claim(decision.customer_reply) and not evidence_present:
        violations.append(
            PolicyViolation(
                code="tracking_status_without_trusted_fact",
                message="Tracking status claims require Speedaf trusted tracking fact evidence.",
                risk_level="high",
            )
        )

    if decision.intent == "tracking" and evidence_present:
        has_trusted_evidence = any(
            item.source in {"speedaf_trusted_tracking_fact", "speedaf.order.query"}
            and bool(item.fact_evidence_present)
            for item in decision.evidence_used
        )
        if not has_trusted_evidence:
            warnings.append("tracking intent had trusted fact metadata but AI evidence_used did not reference it")

    if decision.handoff_required and decision.next_action not in {"request_handoff", "call_tool"}:
        violations.append(
            PolicyViolation(
                code="handoff_next_action_mismatch",
                message="handoff_required=true must use next_action=request_handoff or call_tool.",
                risk_level="medium",
            )
        )
    if (decision.handoff_required or decision.next_action == "request_handoff") and not _handoff_tool_present(decision):
        violations.append(
            PolicyViolation(
                code="handoff_tool_missing",
                message="AI handoff decisions must include handoff.request.create in tool_calls.",
                risk_level="medium",
            )
        )

    for call in decision.tool_calls:
        checked_tools.append(call.tool_name)
        contract = get_tool_contract(call.tool_name)
        if contract is None:
            violations.append(
                PolicyViolation(
                    code="unknown_tool_blocked",
                    message="AI requested an unregistered tool.",
                    tool_name=call.tool_name,
                    risk_level="high",
                )
            )
            continue
        if contract.is_write_tool:
            if contract.allowed_auto_execution_mode == "disabled":
                violations.append(
                    PolicyViolation(
                        code="write_tool_disabled",
                        message="Write/system tool is not allowed in this runtime mode.",
                        tool_name=contract.name,
                        risk_level=contract.risk_level,
                    )
                )
            if contract.confirmation_required and not call.requires_confirmation:
                violations.append(
                    PolicyViolation(
                        code="write_tool_confirmation_required",
                        message="High-risk write tool requires controlled confirmation before execution.",
                        tool_name=contract.name,
                        risk_level=contract.risk_level,
                    )
                )
            if contract.risk_level == "high" and not allow_high_risk_write_execution:
                violations.append(
                    PolicyViolation(
                        code="high_risk_write_tool_blocked",
                        message="High-risk Speedaf write actions are not auto-executed by public WebChat AI in phase one.",
                        tool_name=contract.name,
                        risk_level="high",
                    )
                )
        if contract.classification == "read" and contract.name == "speedaf.order.query" and not (tracking_number or "tracking_number_hash" in call.arguments or "tracking_number" in call.arguments):
            violations.append(
                PolicyViolation(
                    code="tracking_tool_missing_identifier",
                    message="speedaf.order.query requires a trusted tracking identifier supplied by backend context or sanitized arguments.",
                    tool_name=contract.name,
                    risk_level="medium",
                )
            )

    for evidence in decision.evidence_used:
        if evidence.raw_tracking_number_exposed:
            violations.append(
                PolicyViolation(
                    code="evidence_raw_tracking_exposed",
                    message="AI evidence trace cannot mark raw tracking number exposure.",
                    risk_level="high",
                )
            )
        if evidence.tracking_number_hash and not str(evidence.tracking_number_hash).startswith("sha256:"):
            violations.append(
                PolicyViolation(
                    code="invalid_tracking_hash_format",
                    message="Tracking evidence must use sha256 tracking_number_hash format.",
                    risk_level="medium",
                )
            )

    if tracking_number and evidence_present:
        expected_hash = hash_tracking_number(tracking_number)
        metadata_hash = tracking_fact_metadata.get("tracking_number_hash") if isinstance(tracking_fact_metadata, dict) else None
        if metadata_hash and expected_hash and metadata_hash != expected_hash:
            warnings.append("trusted tracking fact hash does not match extracted tracking number hash")

    return PolicyGateResult(ok=not violations, violations=tuple(violations), warnings=tuple(warnings), checked_tools=tuple(checked_tools))
