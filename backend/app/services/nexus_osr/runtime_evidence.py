from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SCHEMA = "nexus.osr.runtime_evidence.v1"
ALLOWED_STATES = ("ready", "degraded", "not_ready", "unavailable")
RUNTIME_PATHS = (
    "runtime_decision",
    "handoff",
    "ticket",
    "tracking",
    "knowledge",
    "dispatch",
    "queue_worker",
    "provider_runtime",
)
STATE_SEVERITY = {"ready": 0, "degraded": 1, "not_ready": 2, "unavailable": 3}
MAX_ARTIFACT_BYTES = 64 * 1024
MAX_PROBE_BYTES = 64 * 1024
MAX_COLLECTION_ITEMS = 100
MAX_SCAN_NODES = 2_000

ALLOWED_REASON_CODES = {
    "probe_ok",
    "identity_expected_missing",
    "identity_observed_missing",
    "identity_invalid",
    "code_drift",
    "config_drift",
    "build_drift",
    "migration_drift",
    "evidence_stale",
    "clock_skew",
    "permission_denied",
    "tenant_scope_missing",
    "tenant_scope_mismatch",
    "source_unavailable",
    "payload_invalid",
    "contradictory_evidence",
    "redaction_failed",
    "unsafe_probe_method",
    "unsafe_probe_url",
    "probe_response_too_large",
    "insufficient_sample",
    "budget_near_exhaustion",
    "budget_exhausted",
    "queue_backlog_high",
    "provider_not_ready",
    "artifact_too_large",
}

_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|credential|password|secret|api[_-]?key|cookie|token|prompt|system_message|"
    r"developer_message|raw(?:_|$)|provider_(?:payload|request|response|body|group_id)|payload|"
    r"tracking_number|phone|email|address|customer_reply|tool_(?:args|arguments|result|results|payload)|"
    r"destination_group_id|fallback_group_id)",
    re.I,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_SECRET_RE = re.compile(
    r"(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"\b(?:password|secret|api[_-]?key|credential)\s*[:=]\s*\S+)",
    re.I,
)
_TRACKING_RE = re.compile(
    r"\b(?=[A-Z0-9._-]{8,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])"
    r"[A-Z0-9][A-Z0-9._-]+\b",
    re.I,
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SAFE_EVIDENCE_KEYS = {
    "tenant_id",
    "state",
    "observed_at",
    "generated_at",
    "evidence_count",
    "age_seconds",
    "code_sha",
    "config_sha256",
    "build_id",
    "migration_head",
    "sha256_prefix",
    "tenant_scope_hash",
}
_SHA_RE = re.compile(r"^[a-fA-F0-9]{7,64}$")
_FORBIDDEN_PROBE_PATH_RE = re.compile(
    r"(?:^|/)(?:send|execute|dispatch-now|publish|delete|remove|create|update|mutate|action)(?:/|$)",
    re.I,
)


@dataclass(frozen=True)
class ReadOnlyProbeSpec:
    path: str
    endpoint: str
    method: str = "GET"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sha256_prefix(value: Any, *, length: int = 16) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()[:length]


def _bounded_token(value: Any, *, sha: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    matcher = _SHA_RE if sha else _SAFE_TOKEN_RE
    if not matcher.fullmatch(text):
        return None
    return text[:128]


def _reason_codes(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value in ALLOWED_REASON_CODES})


def _state_for_reasons(reasons: Iterable[str], *, default: str = "ready") -> str:
    reason_set = set(reasons)
    if reason_set & {
        "identity_expected_missing",
        "identity_observed_missing",
        "permission_denied",
        "source_unavailable",
        "unsafe_probe_method",
        "unsafe_probe_url",
        "probe_response_too_large",
        "artifact_too_large",
    }:
        return "unavailable"
    if reason_set & {
        "identity_invalid",
        "code_drift",
        "config_drift",
        "build_drift",
        "migration_drift",
        "evidence_stale",
        "clock_skew",
        "tenant_scope_missing",
        "tenant_scope_mismatch",
        "payload_invalid",
        "contradictory_evidence",
        "redaction_failed",
        "budget_exhausted",
        "queue_backlog_high",
        "provider_not_ready",
    }:
        return "not_ready"
    if reason_set & {"insufficient_sample", "budget_near_exhaustion"}:
        return "degraded"
    return default


def _safe_identity(identity: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(identity, Mapping):
        return None
    return {
        "code_sha": _bounded_token(identity.get("code_sha"), sha=True),
        "config_sha256": _bounded_token(identity.get("config_sha256"), sha=True),
        "build_id": _bounded_token(identity.get("build_id")),
        "migration_head": _bounded_token(identity.get("migration_head")),
        "observed_at": parse_timestamp(identity.get("observed_at")).isoformat()
        if parse_timestamp(identity.get("observed_at"))
        else None,
    }


def compare_runtime_identity(
    expected: Mapping[str, Any] | None,
    observed: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 900,
) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(timezone.utc)
    reasons: list[str] = []
    expected_safe = _safe_identity(expected)
    observed_safe = _safe_identity(observed)
    if expected_safe is None:
        reasons.append("identity_expected_missing")
    if observed_safe is None:
        reasons.append("identity_observed_missing")
    if reasons:
        return {
            "schema": SCHEMA,
            "state": "unavailable",
            "reason_codes": _reason_codes(reasons),
            "expected": expected_safe,
            "observed": observed_safe,
            "age_seconds": None,
        }

    required = ("code_sha", "config_sha256", "build_id", "migration_head", "observed_at")
    if any(expected_safe.get(key) in (None, "") for key in required[:-1]) or any(
        observed_safe.get(key) in (None, "") for key in required
    ):
        reasons.append("identity_invalid")

    observed_at = parse_timestamp(observed_safe.get("observed_at"))
    age_seconds: int | None = None
    if observed_at is None:
        reasons.append("identity_invalid")
    else:
        age_seconds = math.floor((now - observed_at).total_seconds())
        if age_seconds < -300:
            reasons.append("clock_skew")
        elif age_seconds > max(1, int(max_age_seconds)):
            reasons.append("evidence_stale")

    comparisons = (
        ("code_sha", "code_drift"),
        ("config_sha256", "config_drift"),
        ("build_id", "build_drift"),
        ("migration_head", "migration_drift"),
    )
    for field, reason in comparisons:
        if expected_safe.get(field) and observed_safe.get(field) and expected_safe[field] != observed_safe[field]:
            reasons.append(reason)

    reason_codes = _reason_codes(reasons)
    return {
        "schema": SCHEMA,
        "state": _state_for_reasons(reason_codes),
        "reason_codes": reason_codes or ["probe_ok"],
        "expected": expected_safe,
        "observed": observed_safe,
        "age_seconds": age_seconds,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 6) if denominator > 0 else 0.0


def evaluate_failure_budget(definition: Mapping[str, Any], sample: Mapping[str, Any] | None) -> dict[str, Any]:
    path = str(definition.get("path") or "")
    if path not in RUNTIME_PATHS or not isinstance(sample, Mapping):
        return {
            "schema": SCHEMA,
            "path": path if path in RUNTIME_PATHS else "runtime_decision",
            "state": "unavailable",
            "reason_codes": ["source_unavailable"],
            "owner": _bounded_token(definition.get("owner")) or "unassigned",
            "window_seconds": int(definition.get("window_seconds") or 0),
            "ratios": {"error": 0.0, "unavailable": 0.0, "fail_closed": 0.0},
        }

    requests = max(0, int(sample.get("requests") or 0))
    errors = max(0, int(sample.get("errors") or 0))
    unavailable = max(0, int(sample.get("unavailable") or 0))
    fail_closed = max(0, int(sample.get("fail_closed") or 0))
    redaction_failures = max(0, int(sample.get("redaction_failures") or 0))
    p95_latency_ms = max(0, int(sample.get("p95_latency_ms") or 0))
    backlog = max(0, int(sample.get("backlog") or 0))

    reasons: list[str] = []
    if any(value > requests for value in (errors, unavailable, fail_closed)):
        reasons.append("contradictory_evidence")
    if redaction_failures:
        reasons.append("redaction_failed")

    min_sample_size = max(1, int(definition.get("min_sample_size") or 1))
    if requests < min_sample_size:
        reasons.append("insufficient_sample")

    ratios = {
        "error": _ratio(errors, requests),
        "unavailable": _ratio(unavailable, requests),
        "fail_closed": _ratio(fail_closed, requests),
    }
    thresholds = {
        "error": float(definition.get("max_error_ratio") or 0.0),
        "unavailable": float(definition.get("max_unavailable_ratio") or 0.0),
        "fail_closed": float(definition.get("max_fail_closed_ratio") or 0.0),
    }

    exhausted = any(ratios[kind] > thresholds[kind] for kind in ratios)
    near = any(thresholds[kind] > 0 and ratios[kind] >= thresholds[kind] * 0.8 for kind in ratios)
    if p95_latency_ms > int(definition.get("max_p95_latency_ms") or 0):
        exhausted = True
    max_backlog = int(definition.get("max_backlog") or 0)
    if max_backlog and backlog > max_backlog:
        reasons.append("queue_backlog_high")
        exhausted = True
    if exhausted:
        reasons.append("budget_exhausted")
    elif near:
        reasons.append("budget_near_exhaustion")

    reason_codes = _reason_codes(reasons)
    return {
        "schema": SCHEMA,
        "path": path,
        "state": _state_for_reasons(reason_codes),
        "reason_codes": reason_codes or ["probe_ok"],
        "owner": _bounded_token(definition.get("owner")) or "unassigned",
        "rationale": str(definition.get("rationale") or "")[:240],
        "window_seconds": max(1, int(definition.get("window_seconds") or 1)),
        "sample_size": requests,
        "ratios": ratios,
        "p95_latency_ms": p95_latency_ms,
        "backlog": backlog,
        "redaction_failures": redaction_failures,
    }


def scan_for_unsafe_evidence(payload: Any) -> dict[str, Any]:
    violations: list[str] = []
    nodes = 0

    def visit(value: Any, *, key: str = "", depth: int = 0) -> None:
        nonlocal nodes
        if nodes >= MAX_SCAN_NODES:
            violations.append("payload_invalid")
            return
        nodes += 1
        if depth > 8:
            violations.append("payload_invalid")
            return
        if key and _SENSITIVE_KEY_RE.search(key):
            violations.append("redaction_failed")
            return
        if isinstance(value, Mapping):
            for raw_key, item in list(value.items())[:MAX_COLLECTION_ITEMS]:
                visit(item, key=str(raw_key), depth=depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for item in list(value)[:MAX_COLLECTION_ITEMS]:
                visit(item, key=key, depth=depth + 1)
            return
        if isinstance(value, str):
            if key in _SAFE_EVIDENCE_KEYS:
                return
            text = value[:2_000]
            if _SECRET_RE.search(text) or _EMAIL_RE.search(text) or _PHONE_RE.search(text) or _TRACKING_RE.search(text):
                violations.append("redaction_failed")

    visit(payload)
    reason_codes = _reason_codes(violations)
    return {
        "safe": not reason_codes,
        "reason_codes": reason_codes,
        "nodes_scanned": min(nodes, MAX_SCAN_NODES),
    }


def evaluate_probe_result(
    probe: Mapping[str, Any],
    *,
    expected_tenant_id: str,
    now: datetime | None = None,
    max_age_seconds: int = 900,
) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(timezone.utc)
    path = str(probe.get("path") or "")
    path = path if path in RUNTIME_PATHS else "runtime_decision"
    reasons: list[str] = []
    error_code = str(probe.get("error_code") or "")
    if error_code in ALLOWED_REASON_CODES:
        reasons.append(error_code)
    method = str(probe.get("method") or "GET").upper()
    if method != "GET":
        reasons.append("unsafe_probe_method")
    if not bool(probe.get("permission_granted", False)):
        reasons.append("permission_denied")
    status_code = probe.get("status_code")
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        reasons.append("source_unavailable")

    payload = probe.get("payload")
    if not isinstance(payload, Mapping):
        reasons.append("payload_invalid")
        payload = {}

    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        reasons.append("tenant_scope_missing")
    elif tenant_id != expected_tenant_id:
        reasons.append("tenant_scope_mismatch")

    scan = scan_for_unsafe_evidence(payload)
    reasons.extend(scan["reason_codes"])

    observed_at = parse_timestamp(probe.get("observed_at") or payload.get("observed_at"))
    age_seconds: int | None = None
    if observed_at is None:
        reasons.append("payload_invalid")
    else:
        age_seconds = math.floor((now - observed_at).total_seconds())
        if age_seconds < -300:
            reasons.append("clock_skew")
        elif age_seconds > max(1, int(max_age_seconds)):
            reasons.append("evidence_stale")

    declared_state = str(payload.get("state") or "ready")
    if declared_state not in ALLOWED_STATES:
        reasons.append("payload_invalid")
    elif declared_state in {"not_ready", "unavailable"}:
        reasons.append("provider_not_ready" if path == "provider_runtime" else "source_unavailable")
    elif declared_state == "degraded":
        reasons.append("budget_near_exhaustion")

    if bool(payload.get("contradictory")):
        reasons.append("contradictory_evidence")

    reason_codes = _reason_codes(reasons)
    return {
        "schema": SCHEMA,
        "path": path,
        "state": _state_for_reasons(reason_codes, default=declared_state if declared_state in ALLOWED_STATES else "not_ready"),
        "reason_codes": reason_codes or ["probe_ok"],
        "tenant_scope_hash": sha256_prefix(expected_tenant_id),
        "permission_verified": bool(probe.get("permission_granted", False)),
        "redaction_verified": bool(scan["safe"]),
        "nodes_scanned": scan["nodes_scanned"],
        "age_seconds": age_seconds,
        "evidence_count": min(max(0, int(payload.get("evidence_count") or 0)), MAX_COLLECTION_ITEMS),
    }


def build_runtime_evidence_snapshot(
    *,
    tenant_id: str,
    expected_identity: Mapping[str, Any] | None,
    observed_identity: Mapping[str, Any] | None,
    budget_definitions: Sequence[Mapping[str, Any]],
    samples: Mapping[str, Mapping[str, Any]],
    probes: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
    max_age_seconds: int = 900,
) -> dict[str, Any]:
    if not _SAFE_TOKEN_RE.fullmatch(str(tenant_id or "")):
        raise ValueError("invalid_tenant_scope")
    now = (now or utc_now()).astimezone(timezone.utc)
    identity = compare_runtime_identity(
        expected_identity,
        observed_identity,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    budgets = [
        evaluate_failure_budget(definition, samples.get(str(definition.get("path") or "")))
        for definition in list(budget_definitions)[:MAX_COLLECTION_ITEMS]
    ]
    probe_results = [
        evaluate_probe_result(
            probe,
            expected_tenant_id=tenant_id,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        for probe in list(probes)[:MAX_COLLECTION_ITEMS]
    ]

    states = [identity["state"], *(item["state"] for item in budgets), *(item["state"] for item in probe_results)]
    overall_state = max(states, key=lambda state: STATE_SEVERITY[state]) if states else "unavailable"
    reasons = _reason_codes(
        [
            *identity.get("reason_codes", []),
            *(reason for item in budgets for reason in item.get("reason_codes", [])),
            *(reason for item in probe_results for reason in item.get("reason_codes", [])),
        ]
    )
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "state": overall_state,
        "reason_codes": reasons or ["probe_ok"],
        "tenant_scope_hash": sha256_prefix(tenant_id),
        "identity": identity,
        "failure_budgets": budgets,
        "probes": probe_results,
        "boundaries": {
            "read_only": True,
            "synthetic_or_staging_safe": True,
            "customer_message_sent": False,
            "tool_execution_performed": False,
            "production_mutation_performed": False,
            "raw_payload_retained": False,
            "tenant_scope_exposed_as_metric_label": False,
        },
    }


def render_prometheus_metrics(snapshot: Mapping[str, Any]) -> str:
    lines = [
        "# HELP nexus_osr_runtime_evidence_state Current bounded runtime evidence state.",
        "# TYPE nexus_osr_runtime_evidence_state gauge",
    ]
    overall = str(snapshot.get("state") or "unavailable")
    for state in ALLOWED_STATES:
        lines.append(f'nexus_osr_runtime_evidence_state{{state="{state}"}} {1 if state == overall else 0}')

    identity = snapshot.get("identity") if isinstance(snapshot.get("identity"), Mapping) else {}
    identity_state = str(identity.get("state") or "unavailable")
    lines.extend(
        [
            "# HELP nexus_osr_runtime_identity_state Runtime identity comparison state.",
            "# TYPE nexus_osr_runtime_identity_state gauge",
        ]
    )
    for state in ALLOWED_STATES:
        lines.append(f'nexus_osr_runtime_identity_state{{state="{state}"}} {1 if state == identity_state else 0}')

    lines.extend(
        [
            "# HELP nexus_osr_failure_budget_state Failure budget evaluation state by governed path.",
            "# TYPE nexus_osr_failure_budget_state gauge",
            "# HELP nexus_osr_failure_budget_ratio Aggregate bounded ratio by governed path and kind.",
            "# TYPE nexus_osr_failure_budget_ratio gauge",
            "# HELP nexus_osr_queue_backlog Aggregate queue backlog count.",
            "# TYPE nexus_osr_queue_backlog gauge",
            "# HELP nexus_osr_redaction_failure_detected Redaction failure fail-closed indicator.",
            "# TYPE nexus_osr_redaction_failure_detected gauge",
        ]
    )
    redaction_failure = 0
    for item in snapshot.get("failure_budgets") or []:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "")
        state = str(item.get("state") or "unavailable")
        if path not in RUNTIME_PATHS or state not in ALLOWED_STATES:
            continue
        lines.append(f'nexus_osr_failure_budget_state{{path="{path}",state="{state}"}} 1')
        ratios = item.get("ratios") if isinstance(item.get("ratios"), Mapping) else {}
        for kind in ("error", "unavailable", "fail_closed"):
            value = float(ratios.get(kind) or 0.0)
            lines.append(f'nexus_osr_failure_budget_ratio{{path="{path}",kind="{kind}"}} {value:.6f}')
        if path == "queue_worker":
            lines.append(f'nexus_osr_queue_backlog{{path="queue_worker"}} {int(item.get("backlog") or 0)}')
        redaction_failure = max(redaction_failure, 1 if int(item.get("redaction_failures") or 0) > 0 else 0)

    lines.extend(
        [
            "# HELP nexus_osr_probe_state Read-only or synthetic probe state by governed path.",
            "# TYPE nexus_osr_probe_state gauge",
        ]
    )
    for item in snapshot.get("probes") or []:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "")
        state = str(item.get("state") or "unavailable")
        if path not in RUNTIME_PATHS or state not in ALLOWED_STATES:
            continue
        lines.append(f'nexus_osr_probe_state{{path="{path}",state="{state}"}} 1')
        if not bool(item.get("redaction_verified", False)):
            redaction_failure = 1
    lines.append(f"nexus_osr_redaction_failure_detected {redaction_failure}")
    return "\n".join(lines) + "\n"


def bounded_json_bytes(payload: Mapping[str, Any], *, max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(encoded) > max_bytes:
        fallback = {
            "schema": SCHEMA,
            "state": "unavailable",
            "reason_codes": ["artifact_too_large"],
            "boundaries": {
                "read_only": True,
                "customer_message_sent": False,
                "tool_execution_performed": False,
                "production_mutation_performed": False,
                "raw_payload_retained": False,
            },
        }
        return json.dumps(fallback, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return encoded


def validate_read_only_probe_url(base_url: str, endpoint: str, *, allowed_hosts: Sequence[str]) -> str:
    parsed_base = urlparse(base_url)
    host = (parsed_base.hostname or "").lower()
    if parsed_base.scheme not in {"https", "http"}:
        raise ValueError("unsafe_probe_url")
    if parsed_base.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("unsafe_probe_url")
    if host not in {item.lower() for item in allowed_hosts}:
        raise ValueError("unsafe_probe_url")
    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.scheme or parsed_endpoint.netloc:
        raise ValueError("unsafe_probe_url")
    if not endpoint.startswith("/") or _FORBIDDEN_PROBE_PATH_RE.search(parsed_endpoint.path):
        raise ValueError("unsafe_probe_url")
    return urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))


def run_read_only_http_probe(
    spec: ReadOnlyProbeSpec,
    *,
    base_url: str,
    allowed_hosts: Sequence[str],
    tenant_id: str,
    bearer_token: str,
    timeout_seconds: float = 5.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    if spec.method.upper() != "GET":
        return {
            "path": spec.path,
            "method": spec.method,
            "permission_granted": False,
            "status_code": 0,
            "payload": {},
            "observed_at": utc_now().isoformat(),
            "error_code": "unsafe_probe_method",
        }
    try:
        url = validate_read_only_probe_url(base_url, spec.endpoint, allowed_hosts=allowed_hosts)
    except ValueError:
        return {
            "path": spec.path,
            "method": "GET",
            "permission_granted": False,
            "status_code": 0,
            "payload": {},
            "observed_at": utc_now().isoformat(),
            "error_code": "unsafe_probe_url",
        }

    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token}",
            "X-Nexus-Tenant": tenant_id,
        },
    )
    try:
        with opener(request, timeout=max(0.1, min(float(timeout_seconds), 15.0))) as response:
            body = response.read(MAX_PROBE_BYTES + 1)
            status_code = int(getattr(response, "status", 0) or 0)
        if len(body) > MAX_PROBE_BYTES:
            return {
                "path": spec.path,
                "method": "GET",
                "permission_granted": True,
                "status_code": status_code,
                "payload": {},
                "observed_at": utc_now().isoformat(),
                "error_code": "probe_response_too_large",
            }
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, Mapping):
            payload = {}
        return {
            "path": spec.path,
            "method": "GET",
            "permission_granted": True,
            "status_code": status_code,
            "payload": payload,
            "observed_at": utc_now().isoformat(),
        }
    except Exception:
        return {
            "path": spec.path,
            "method": "GET",
            "permission_granted": True,
            "status_code": 0,
            "payload": {},
            "observed_at": utc_now().isoformat(),
            "error_code": "source_unavailable",
        }
