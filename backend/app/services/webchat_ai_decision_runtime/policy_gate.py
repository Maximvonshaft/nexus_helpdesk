from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..customer_visible_policy import evaluate_customer_visible_policy, format_policy_reasons
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
            "violations": [item.__dict__ for item in self.violations],
            "warnings": list(self.warnings),
            "checked_tools": list(self.checked_tools),
        }


def validate_ai_decision(
    decision: AIDecision,
    *,
    allow_high_risk_write_execution: bool = False,
    allowed_high_risk_write_tools: set[str] | frozenset[str] | None = None,
    granted_permissions: set[str] | frozenset[str] | None = None,
    customer_confirmation_granted: bool = False,
    human_confirmation_granted: bool = False,
    enforce_confirmation_requirements: bool = True,
    **_legacy: Any,
) -> PolicyGateResult:
    """Validate generic Agent output and server-owned Tool authority.

    Domain truth is owned by Skills and Tool observations. This gate enforces
    platform controls only. Model-produced confirmation flags are descriptive
    output and never authorize execution.
    """

    violations: list[PolicyViolation] = []
    warnings: list[str] = []
    checked_tools: list[str] = []
    allowed_high_risk = set(allowed_high_risk_write_tools or set())
    permissions = (
        None
        if granted_permissions is None
        else set(granted_permissions)
    )
    trusted_confirmation = bool(
        customer_confirmation_granted or human_confirmation_granted
    )

    if decision.customer_reply:
        customer_policy = evaluate_customer_visible_policy(decision.customer_reply)
        if not customer_policy.allowed:
            violations.append(
                PolicyViolation(
                    code="unsafe_customer_reply",
                    message=format_policy_reasons(customer_policy),
                    risk_level="high",
                )
            )

    for call in decision.tool_calls:
        checked_tools.append(call.tool_name)
        contract = get_tool_contract(call.tool_name)
        if contract is None:
            violations.append(
                PolicyViolation(
                    code="unknown_tool_blocked",
                    message="The Agent requested an unregistered Tool.",
                    tool_name=call.tool_name,
                    risk_level="high",
                )
            )
            continue
        _validate_contract_authority(
            contract,
            trusted_confirmation=trusted_confirmation,
            allow_high_risk_write_execution=allow_high_risk_write_execution,
            allowed_high_risk=allowed_high_risk,
            permissions=permissions,
            enforce_confirmation_requirements=(
                enforce_confirmation_requirements
            ),
            violations=violations,
            warnings=warnings,
        )

    return PolicyGateResult(
        ok=not violations,
        violations=tuple(violations),
        warnings=tuple(warnings),
        checked_tools=tuple(checked_tools),
    )


def _validate_contract_authority(
    contract: ToolContract,
    *,
    trusted_confirmation: bool,
    allow_high_risk_write_execution: bool,
    allowed_high_risk: set[str],
    permissions: set[str] | None,
    enforce_confirmation_requirements: bool,
    violations: list[PolicyViolation],
    warnings: list[str],
) -> None:
    if contract.allowed_auto_execution_mode == "disabled":
        violations.append(
            PolicyViolation(
                code="tool_disabled",
                message="The requested Tool is disabled.",
                tool_name=contract.name,
                risk_level=contract.risk_level,
            )
        )
    if (
        permissions is not None
        and contract.required_permissions
        and not set(contract.required_permissions).issubset(permissions)
    ):
        violations.append(
            PolicyViolation(
                code="tool_permission_denied",
                message="The runtime does not have the required Tool permission.",
                tool_name=contract.name,
                risk_level=contract.risk_level,
            )
        )
    if (
        enforce_confirmation_requirements
        and contract.confirmation_required
        and not trusted_confirmation
    ):
        violations.append(
            PolicyViolation(
                code="write_tool_confirmation_required",
                message="The requested action requires trusted server-side confirmation.",
                tool_name=contract.name,
                risk_level=contract.risk_level,
            )
        )
    if contract.is_write_tool and contract.risk_level == "high":
        if (
            not allow_high_risk_write_execution
            or contract.name not in allowed_high_risk
        ):
            violations.append(
                PolicyViolation(
                    code="high_risk_write_tool_blocked",
                    message="High-risk write execution is not enabled for this Tool.",
                    tool_name=contract.name,
                    risk_level="high",
                )
            )
    elif (
        contract.is_write_tool
        and contract.allowed_auto_execution_mode == "policy_gated"
    ):
        warnings.append(f"policy_gated_write:{contract.name}")
