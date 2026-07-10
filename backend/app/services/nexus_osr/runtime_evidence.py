from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RuntimeEvidenceState(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"
    UNAVAILABLE = "unavailable"


class AlertSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


LOW_CARDINALITY_LABELS = {
    "path",
    "runtime_path",
    "state",
    "status",
    "reason_code",
    "severity",
    "tenant_scope",
    "permission_scope",
    "queue",
}
MAX_LABEL_VALUE_LENGTH = 64
MAX_REPORT_REASONS = 20
MAX_SAFE_STRING = 160
_TRACKING_RE = re.compile(r"\b[A-Z]{2,4}\d{7,16}\b")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-\s().]{7,}\d)(?!\d)")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|credential|provider[_-]?payload|raw[_-]?prompt|tool[_-]?args?)\s*[:=]\s*[^,\s}]+"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_digest(value: str | None, *, length: int = 12) -> str:
    if value is None:
        return "none"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def redact_runtime_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k)[:MAX_LABEL_VALUE_LENGTH]: redact_runtime_evidence(v) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [redact_runtime_evidence(item) for item in value[:25]]
    if not isinstance(value, str):
        return value
    safe = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    safe = _EMAIL_RE.sub("[redacted-email]", safe)
    safe = _PHONE_RE.sub("[redacted-phone]", safe)
    safe = _TRACKING_RE.sub(lambda match: f"[tracking:{stable_digest(match.group(0), length=8)}]", safe)
    if len(safe) > MAX_SAFE_STRING:
        return f"{safe[:MAX_SAFE_STRING]}…"
    return safe


@dataclass(frozen=True)
class RuntimeIdentity:
    code_sha: str
    config_fingerprint: str
    migration_head: str
    image_tag: str | None = None

    def redacted(self) -> dict[str, str | None]:
        return {
            "code_sha": self.code_sha[:12],
            "config_fingerprint": stable_digest(self.config_fingerprint),
            "migration_head": self.migration_head,
            "image_tag_hash": stable_digest(self.image_tag) if self.image_tag else None,
        }


@dataclass(frozen=True)
class FailureBudget:
    key: str
    runtime_path: str
    owner: str
    max_failure_rate: float
    max_unavailable_minutes: int
    rationale: str

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not self.key or not re.fullmatch(r"[a-z0-9_.-]{3,80}", self.key):
            issues.append("invalid_failure_budget_key")
        if not self.owner:
            issues.append(f"{self.key}:missing_owner")
        if not 0 <= self.max_failure_rate <= 1:
            issues.append(f"{self.key}:invalid_failure_rate")
        if self.max_unavailable_minutes < 0:
            issues.append(f"{self.key}:invalid_unavailable_window")
        if not self.rationale:
            issues.append(f"{self.key}:missing_rationale")
        return issues


@dataclass(frozen=True)
class RuntimeSignal:
    key: str
    runtime_path: str
    state: RuntimeEvidenceState
    failure_rate: float = 0.0
    unavailable_minutes: int = 0
    stale_minutes: int = 0
    audit_available: bool = True
    redaction_ok: bool = True
    queue_backlog: int = 0
    reason_code: str = "ok"


@dataclass(frozen=True)
class AlertRule:
    key: str
    reason_code: str
    severity: AlertSeverity
    owner: str
    threshold: str
    runbook: str
    labels: dict[str, str] = field(default_factory=dict)

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not re.fullmatch(r"[a-z0-9_.-]{3,80}", self.key):
            issues.append("invalid_alert_key")
        if not re.fullmatch(r"[a-z0-9_.-]{2,80}", self.reason_code):
            issues.append(f"{self.key}:invalid_reason_code")
        if not self.owner:
            issues.append(f"{self.key}:missing_owner")
        if not self.threshold:
            issues.append(f"{self.key}:missing_threshold")
        if not self.runbook.startswith("docs/"):
            issues.append(f"{self.key}:runbook_must_be_repo_doc")
        for label, value in self.labels.items():
            if label not in LOW_CARDINALITY_LABELS:
                issues.append(f"{self.key}:high_cardinality_label:{label}")
            if len(str(value)) > MAX_LABEL_VALUE_LENGTH:
                issues.append(f"{self.key}:label_value_too_long:{label}")
        return issues


@dataclass(frozen=True)
class StagingProbeEvidence:
    key: str
    runtime_path: str
    state: RuntimeEvidenceState
    tenant_scope: str
    permission_scope: str
    read_only: bool
    synthetic: bool
    evidence_fresh: bool
    redaction_ok: bool
    details: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not self.read_only:
            issues.append(f"{self.key}:probe_not_read_only")
        if not self.synthetic:
            issues.append(f"{self.key}:probe_not_synthetic")
        if not self.tenant_scope:
            issues.append(f"{self.key}:missing_tenant_scope")
        if not self.permission_scope:
            issues.append(f"{self.key}:missing_permission_scope")
        if not self.evidence_fresh:
            issues.append(f"{self.key}:stale_evidence")
        if not self.redaction_ok:
            issues.append(f"{self.key}:redaction_failed")
        return issues


def default_failure_budgets() -> list[FailureBudget]:
    return [
        FailureBudget("runtime.audit", "runtime_decision_audit", "OSR runtime owner", 0.01, 5, "Audit evidence is mandatory for governed decisions."),
        FailureBudget("handoff.ticket", "handoff_auto_ticket", "OSR operations owner", 0.02, 10, "Human handoff and offline tickets must stay available."),
        FailureBudget("tracking.truth", "tracking_truth_layer", "OSR tracking owner", 0.01, 5, "Live shipment facts must remain authoritative and fail closed."),
        FailureBudget("knowledge.boundary", "knowledge_runtime", "OSR knowledge owner", 0.02, 15, "Knowledge can assist only when safe evidence is available."),
        FailureBudget("dispatch.outbox", "operations_dispatch_outbox", "OSR channel owner", 0.02, 10, "Operations dispatch must expose retryable bounded failure states."),
    ]


def default_alert_rules() -> list[AlertRule]:
    runbook = "docs/ops/NEXUS_OSR_RUNTIME_EVIDENCE_RUNBOOK.md"
    return [
        AlertRule("audit.unavailable", "audit_unavailable", AlertSeverity.CRITICAL, "OSR runtime owner", "audit_available == false", runbook, {"runtime_path": "runtime_decision_audit", "severity": "critical"}),
        AlertRule("evidence.stale", "stale_evidence", AlertSeverity.WARNING, "OSR runtime owner", "stale_minutes > budget", runbook, {"reason_code": "stale_evidence", "severity": "warning"}),
        AlertRule("redaction.failure", "redaction_failed", AlertSeverity.CRITICAL, "OSR privacy owner", "redaction_ok == false", runbook, {"reason_code": "redaction_failed", "severity": "critical"}),
        AlertRule("queue.backlog", "queue_backlog", AlertSeverity.WARNING, "OSR operations owner", "queue_backlog > 100", runbook, {"queue": "operator", "severity": "warning"}),
        AlertRule("runtime.drift", "runtime_identity_drift", AlertSeverity.CRITICAL, "OSR release owner", "identity_mismatch == true", runbook, {"reason_code": "runtime_identity_drift", "severity": "critical"}),
    ]


def compare_runtime_identity(expected: RuntimeIdentity, observed: RuntimeIdentity | None) -> tuple[RuntimeEvidenceState, list[str], dict[str, Any]]:
    if observed is None:
        return RuntimeEvidenceState.UNAVAILABLE, ["runtime_identity_unavailable"], {"expected": expected.redacted(), "observed": None}
    reasons: list[str] = []
    if expected.code_sha != observed.code_sha:
        reasons.append("code_sha_drift")
    if expected.config_fingerprint != observed.config_fingerprint:
        reasons.append("config_fingerprint_drift")
    if expected.migration_head != observed.migration_head:
        reasons.append("migration_head_drift")
    if (expected.image_tag or "") != (observed.image_tag or ""):
        reasons.append("image_tag_drift")
    state = RuntimeEvidenceState.READY if not reasons else RuntimeEvidenceState.NOT_READY
    return state, reasons, {"expected": expected.redacted(), "observed": observed.redacted()}


def evaluate_runtime_signals(signals: list[RuntimeSignal], budgets: list[FailureBudget]) -> tuple[RuntimeEvidenceState, list[str], list[dict[str, Any]]]:
    budget_by_path = {budget.runtime_path: budget for budget in budgets}
    reasons: list[str] = []
    rows: list[dict[str, Any]] = []
    worst = RuntimeEvidenceState.READY
    for signal in signals:
        budget = budget_by_path.get(signal.runtime_path)
        signal_reasons: list[str] = []
        if signal.state == RuntimeEvidenceState.UNAVAILABLE or not signal.audit_available:
            signal_reasons.append("audit_or_signal_unavailable")
        if not signal.redaction_ok:
            signal_reasons.append("redaction_failed")
        if budget and signal.failure_rate > budget.max_failure_rate:
            signal_reasons.append("failure_budget_exceeded")
        if budget and signal.unavailable_minutes > budget.max_unavailable_minutes:
            signal_reasons.append("unavailable_budget_exceeded")
        if signal.stale_minutes > (budget.max_unavailable_minutes if budget else 10):
            signal_reasons.append("stale_evidence")
        if signal.queue_backlog > 100:
            signal_reasons.append("queue_backlog")
        if signal_reasons:
            worst = RuntimeEvidenceState.NOT_READY
            reasons.extend(f"{signal.key}:{reason}" for reason in signal_reasons)
        elif signal.state == RuntimeEvidenceState.DEGRADED and worst == RuntimeEvidenceState.READY:
            worst = RuntimeEvidenceState.DEGRADED
        rows.append(
            {
                "key": signal.key,
                "runtime_path": signal.runtime_path,
                "state": signal.state.value,
                "reason_code": signal.reason_code,
                "reasons": signal_reasons[:5],
            }
        )
    return worst, reasons[:MAX_REPORT_REASONS], rows


def evaluate_staging_probes(probes: list[StagingProbeEvidence]) -> tuple[RuntimeEvidenceState, list[str], list[dict[str, Any]]]:
    reasons: list[str] = []
    rows: list[dict[str, Any]] = []
    worst = RuntimeEvidenceState.READY
    for probe in probes:
        probe_reasons = probe.validate()
        if probe.state in {RuntimeEvidenceState.NOT_READY, RuntimeEvidenceState.UNAVAILABLE}:
            probe_reasons.append(f"probe_{probe.state.value}")
        if probe_reasons:
            worst = RuntimeEvidenceState.NOT_READY
            reasons.extend(probe_reasons)
        elif probe.state == RuntimeEvidenceState.DEGRADED and worst == RuntimeEvidenceState.READY:
            worst = RuntimeEvidenceState.DEGRADED
        rows.append(
            {
                "key": probe.key,
                "runtime_path": probe.runtime_path,
                "state": probe.state.value,
                "tenant_scope": probe.tenant_scope,
                "permission_scope": probe.permission_scope,
                "read_only": probe.read_only,
                "synthetic": probe.synthetic,
                "details": redact_runtime_evidence(probe.details),
            }
        )
    return worst, reasons[:MAX_REPORT_REASONS], rows


def build_runtime_evidence_report(
    *,
    expected_identity: RuntimeIdentity,
    observed_identity: RuntimeIdentity | None,
    signals: list[RuntimeSignal],
    probes: list[StagingProbeEvidence],
    budgets: list[FailureBudget] | None = None,
    alerts: list[AlertRule] | None = None,
) -> dict[str, Any]:
    budgets = budgets or default_failure_budgets()
    alerts = alerts or default_alert_rules()
    budget_errors = [error for budget in budgets for error in budget.validate()]
    alert_errors = [error for alert in alerts for error in alert.validate()]
    identity_state, identity_reasons, identity_payload = compare_runtime_identity(expected_identity, observed_identity)
    signal_state, signal_reasons, signal_rows = evaluate_runtime_signals(signals, budgets)
    probe_state, probe_reasons, probe_rows = evaluate_staging_probes(probes)
    reasons = [*budget_errors, *alert_errors, *identity_reasons, *signal_reasons, *probe_reasons][:MAX_REPORT_REASONS]
    if budget_errors or alert_errors or RuntimeEvidenceState.NOT_READY in {identity_state, signal_state, probe_state}:
        overall = RuntimeEvidenceState.NOT_READY
    elif RuntimeEvidenceState.UNAVAILABLE in {identity_state, signal_state, probe_state}:
        overall = RuntimeEvidenceState.UNAVAILABLE
    elif RuntimeEvidenceState.DEGRADED in {identity_state, signal_state, probe_state}:
        overall = RuntimeEvidenceState.DEGRADED
    else:
        overall = RuntimeEvidenceState.READY
    return {
        "schema": "nexus.osr.runtime_evidence.v1",
        "generated_at": _utc_now_iso(),
        "state": overall.value,
        "reason_count": len(reasons),
        "reasons": reasons,
        "identity": identity_payload,
        "failure_budgets": [
            {
                "key": budget.key,
                "runtime_path": budget.runtime_path,
                "owner": budget.owner,
                "max_failure_rate": budget.max_failure_rate,
                "max_unavailable_minutes": budget.max_unavailable_minutes,
                "rationale": budget.rationale,
            }
            for budget in budgets
        ],
        "alerts": [
            {
                "key": alert.key,
                "reason_code": alert.reason_code,
                "severity": alert.severity.value,
                "owner": alert.owner,
                "threshold": alert.threshold,
                "runbook": alert.runbook,
                "labels": alert.labels,
            }
            for alert in alerts
        ],
        "signals": signal_rows,
        "staging_probes": probe_rows,
        "not_verified": ["actual_staging_probe_execution_requires_separate_authorization"],
    }


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(redact_runtime_evidence(report), ensure_ascii=False, indent=2, sort_keys=True)
