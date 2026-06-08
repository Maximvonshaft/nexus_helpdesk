#!/usr/bin/env python3
"""Production-safe Codex Direct WebChat latency benchmark.

Smoke mode calls the local WebChat Fast endpoint. Audit mode summarizes
provider_runtime_audit_logs. The script writes artifacts only and does not
print secrets or auth material.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_URL = "http://127.0.0.1:18081/api/webchat/fast-reply"
TRACKING_RE = re.compile(r"\b(?=[A-Z0-9._-]{8,48}\b)(?=[A-Z0-9._-]*\d)[A-Z0-9][A-Z0-9._-]+\b", re.I)
LONG_NUMERIC_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")
TEMPORARILY_UNAVAILABLE_TERMS = ("temporarily unavailable", "暂时不可用")
LIVE_STATUS_TERMS = (
    "delivered",
    "in transit",
    "out for delivery",
    "customs",
    "returned",
    "已签收",
    "运输中",
    "派送中",
    "清关",
    "退回",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:80] or "codex_direct_latency"


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def percentile(values: Sequence[int | float], q: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    return clean[lower] + (clean[upper] - clean[lower]) * (position - lower)


def metric(values: Iterable[Any]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"p50": None, "p95": None, "max": None}
    return {"p50": percentile(clean, 0.50), "p95": percentile(clean, 0.95), "max": max(clean)}


def tracking_candidates(body: str) -> list[str]:
    candidates = [match.group(0) for match in TRACKING_RE.finditer(body or "")]
    candidates.extend(match.group(0) for match in LONG_NUMERIC_RE.finditer(body or ""))
    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        key = re.sub(r"[^A-Z0-9]", "", item.upper())
        if len(key) >= 8 and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def raw_identifier_leak_paths(value: Any, identifiers: Sequence[str], *, root: str = "$") -> list[str]:
    exact_identifiers = _exact_identifier_needles(identifiers)
    paths: list[str] = []

    def visit(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                visit(child, f"{path}.{_safe_path_key(key)}")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
            return
        if isinstance(item, str) and _string_contains_any_identifier(item, exact_identifiers):
            paths.append(path)

    visit(value, root)
    return sorted(set(paths))


def _exact_identifier_needles(identifiers: Sequence[str]) -> list[str]:
    needles: list[str] = []
    seen: set[str] = set()
    for identifier in identifiers:
        raw = str(identifier or "").strip()
        digits = re.sub(r"\D", "", raw)
        candidates = [raw]
        if len(digits) >= 8:
            candidates.append(digits)
        for candidate in candidates:
            key = candidate.upper()
            if len(candidate) >= 8 and key not in seen:
                seen.add(key)
                needles.append(candidate)
    return needles


def _string_contains_any_identifier(value: str, identifiers: Sequence[str]) -> bool:
    for identifier in identifiers:
        if re.search(re.escape(identifier), value, flags=re.IGNORECASE):
            return True
    return False


def _safe_path_key(key: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_:-]", "_", str(key))[:80]
    return text or "field"


def contains_any(text: str | None, terms: Sequence[str]) -> bool:
    lowered = (text or "").lower()
    return any(term.lower() in lowered for term in terms)


def latest_codex_audit(audit_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in audit_rows:
        if str(row.get("provider") or "") == "codex_direct":
            return dict(row)
    return {}


def extract_codex_fields(audit_row: Mapping[str, Any]) -> dict[str, Any]:
    safe_summary = parse_json_dict(audit_row.get("safe_summary"))
    latency = parse_json_dict(safe_summary.get("latency"))
    codex_total_ms = as_int(latency.get("total_ms"))
    subprocess_ms = as_int(latency.get("subprocess_ms"))
    prompt_chars = as_int(safe_summary.get("prompt_chars"))
    return {
        "codex_elapsed_ms": as_int(audit_row.get("elapsed_ms")),
        "codex_latency_total_ms": codex_total_ms,
        "codex_latency_subprocess_ms": subprocess_ms,
        "codex_prompt_chars": prompt_chars,
        "codex_total_ms": codex_total_ms,
        "subprocess_ms": subprocess_ms,
        "prompt_chars": prompt_chars,
        "stdout_chars": as_int(safe_summary.get("stdout_chars")),
        "stderr_chars": as_int(safe_summary.get("stderr_chars")),
        "readiness_cache_hit": safe_summary.get("readiness_cache_hit"),
        "readiness_cache_ttl_seconds": as_int(safe_summary.get("readiness_cache_ttl_seconds")),
        "auth_mtime_present": safe_summary.get("auth_mtime_present"),
        "readiness_ms": as_int(safe_summary.get("readiness_ms") or latency.get("readiness_ms")),
    }


def extract_phase_timings(payload: Mapping[str, Any]) -> dict[str, Any]:
    trace = payload.get("ai_decision_trace") if isinstance(payload.get("ai_decision_trace"), Mapping) else {}
    timings = trace.get("phase_timings") if isinstance(trace.get("phase_timings"), Mapping) else payload.get("latency_trace")
    timings = timings if isinstance(timings, Mapping) else {}
    return {
        "tracking_fact_ms": as_int(timings.get("tracking_fact_elapsed_ms")),
        "runtime_context_ms": as_int(timings.get("runtime_context_elapsed_ms")),
        "provider_ms": as_int(timings.get("provider_elapsed_ms") or timings.get("provider_wall_elapsed_ms")),
        "provider_wall_ms": as_int(timings.get("provider_wall_elapsed_ms")),
        "policy_gate_ms": as_int(timings.get("policy_gate_elapsed_ms")),
        "total_elapsed_ms": as_int(timings.get("total_elapsed_ms")),
    }


def build_smoke_headers(origin: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if origin and origin.strip():
        headers["Origin"] = origin.strip()
    return headers


def query_provider_audit_rows(*, database_url: str | None, session_id: str | None, limit: int) -> list[dict[str, Any]]:
    if not database_url:
        return []
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    where = ["provider = :provider"]
    params: dict[str, Any] = {"provider": "codex_direct", "limit": limit}
    if session_id:
        where.append("session_id = :session_id")
        params["session_id"] = session_id
    sql = text(
        """
        SELECT created_at, request_id, session_id, provider, operation, status, error_code, elapsed_ms, safe_summary
        FROM provider_runtime_audit_logs
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit
        """.format(where_clause=" AND ".join(where))
    )
    with engine.connect() as connection:
        rows = connection.execute(sql, params).mappings().all()
    return [dict(row) for row in rows]


def audit_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    from sqlalchemy import create_engine, text

    if not args.database_url:
        raise SystemExit("--database-url or DATABASE_URL is required for audit mode")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes) if args.since_minutes else None
    where = ["provider = 'codex_direct'"]
    params: dict[str, Any] = {"limit": args.iterations}
    if cutoff:
        where.append("created_at >= :cutoff")
        params["cutoff"] = cutoff
    sql = text(
        """
        SELECT created_at, request_id, session_id, provider, operation, status, error_code, elapsed_ms, safe_summary
        FROM provider_runtime_audit_logs
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit
        """.format(where_clause=" AND ".join(where))
    )
    engine = create_engine(args.database_url)
    with engine.connect() as connection:
        rows = connection.execute(sql, params).mappings().all()
    records = []
    for index, row in enumerate(rows):
        audit_row = dict(row)
        codex = extract_codex_fields(audit_row)
        status = str(audit_row.get("status") or "").strip().lower()
        records.append(
            {
                "timestamp": str(audit_row.get("created_at") or utc_now()),
                "mode": "audit",
                "label": args.label,
                "sample_index": index,
                "session_id": audit_row.get("session_id"),
                "client_message_id": None,
                "http_status": None,
                "elapsed_ms": as_int(audit_row.get("elapsed_ms")),
                "reply": "",
                "reply_source": "codex_direct",
                "intent": None,
                "server_safe_fallback": False,
                "temporarily_unavailable_leaked": False,
                "reply_raw_identifier_leaked": False,
                "payload_raw_identifier_leaked": False,
                "raw_identifier_leak_paths": [],
                "raw_identifier_leaked": False,
                "live_status_claim_leaked": False,
                "tracking_fact_evidence_present": None,
                "policy_gate_ok": None,
                "policy_gate_violations": [],
                "provider_audit_rows": [audit_row],
                "provider_audit_available": True,
                "provider_audit_count": 1,
                "repair_applied": None,
                "codex_timeout": audit_row.get("error_code") == "codex_direct_timeout",
                "timeout": audit_row.get("error_code") == "codex_direct_timeout",
                "effective_success": status == "ok" and audit_row.get("error_code") is None,
                "tracking_fact_ms": None,
                "runtime_context_ms": None,
                "provider_ms": as_int(audit_row.get("elapsed_ms")),
                "provider_wall_ms": None,
                "policy_gate_ms": None,
                "total_elapsed_ms": None,
                **codex,
            }
        )
    return records


def smoke_once(args: argparse.Namespace, index: int) -> dict[str, Any]:
    session_id = f"codex-latency-{safe_label(args.label)}-{uuid.uuid4().hex[:10]}"
    client_message_id = f"bench-{index}-{uuid.uuid4().hex[:8]}"
    payload = {
        "tenant_key": args.tenant_key,
        "channel_key": args.channel_key,
        "session_id": session_id,
        "client_message_id": client_message_id,
        "body": args.body,
        "recent_context": [],
        "country_code": args.country_code,
    }
    if getattr(args, "require_provider_audit", False) and not args.database_url:
        raise SystemExit("--require-provider-audit requires --database-url or DATABASE_URL")
    identifiers = tracking_candidates(args.body)
    request = urllib.request.Request(
        args.endpoint_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=build_smoke_headers(args.origin),
        method="POST",
    )
    started = time.monotonic()
    http_status = None
    response_payload: dict[str, Any] = {}
    error_code = None
    try:
        with urllib.request.urlopen(request, timeout=args.timeout_seconds) as response:
            http_status = response.status
            response_payload = parse_json_dict(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        response_payload = parse_json_dict(exc.read().decode("utf-8", errors="replace"))
        error_code = str(response_payload.get("error_code") or exc.reason)
    except Exception as exc:
        error_code = type(exc).__name__
    elapsed_ms = int((time.monotonic() - started) * 1000)

    audit_rows = query_provider_audit_rows(database_url=args.database_url, session_id=session_id, limit=10)
    provider_audit_available = bool(audit_rows)
    if getattr(args, "require_provider_audit", False) and not provider_audit_available:
        raise SystemExit(f"--require-provider-audit found no provider_runtime_audit_logs rows for session_id={session_id}")
    codex_row = latest_codex_audit(audit_rows)
    codex = extract_codex_fields(codex_row) if codex_row else {
        "codex_elapsed_ms": None,
        "codex_latency_total_ms": None,
        "codex_latency_subprocess_ms": None,
        "codex_prompt_chars": None,
        "codex_total_ms": None,
        "subprocess_ms": None,
        "prompt_chars": None,
        "stdout_chars": None,
        "stderr_chars": None,
        "readiness_cache_hit": None,
        "readiness_cache_ttl_seconds": None,
        "auth_mtime_present": None,
        "readiness_ms": None,
    }
    trace = response_payload.get("ai_decision_trace") if isinstance(response_payload.get("ai_decision_trace"), dict) else {}
    policy = trace.get("policy_gate") if isinstance(trace.get("policy_gate"), dict) else {}
    tracking_fact = response_payload.get("tracking_fact") if isinstance(response_payload.get("tracking_fact"), dict) else {}
    reply = response_payload.get("reply") if isinstance(response_payload.get("reply"), str) else ""
    temporarily_unavailable = contains_any(reply, TEMPORARILY_UNAVAILABLE_TERMS)
    reply_leak_paths = raw_identifier_leak_paths(reply, identifiers, root="$.reply")
    payload_leak_paths = raw_identifier_leak_paths(response_payload, identifiers)
    reply_raw_identifier_leaked = bool(reply_leak_paths)
    payload_raw_identifier_leaked = bool(payload_leak_paths)
    live_status_claim_leaked = contains_any(reply, LIVE_STATUS_TERMS)
    codex_timeout = error_code == "codex_direct_timeout" or any(row.get("error_code") == "codex_direct_timeout" for row in audit_rows)
    phase_timings = extract_phase_timings(response_payload)
    server_safe_fallback = response_payload.get("reply_source") == "server_safe_fallback"
    http_success = http_status == 200 and not error_code
    policy_gate_ok = policy.get("ok")
    effective_success = bool(
        http_success
        and bool(reply.strip())
        and not server_safe_fallback
        and not temporarily_unavailable
        and not live_status_claim_leaked
        and not reply_raw_identifier_leaked
        and not codex_timeout
        and policy_gate_ok is not False
    )
    return {
        "timestamp": utc_now(),
        "mode": "smoke",
        "label": args.label,
        "sample_index": index,
        "session_id": session_id,
        "client_message_id": client_message_id,
        "http_status": http_status,
        "elapsed_ms": elapsed_ms,
        "reply": reply,
        "reply_source": response_payload.get("reply_source"),
        "intent": response_payload.get("intent"),
        "server_safe_fallback": server_safe_fallback,
        "temporarily_unavailable_leaked": temporarily_unavailable,
        "reply_raw_identifier_leaked": reply_raw_identifier_leaked,
        "payload_raw_identifier_leaked": payload_raw_identifier_leaked,
        "raw_identifier_leak_paths": payload_leak_paths,
        "raw_identifier_leaked": reply_raw_identifier_leaked,
        "live_status_claim_leaked": live_status_claim_leaked,
        "tracking_fact_evidence_present": tracking_fact.get("fact_evidence_present"),
        "policy_gate_ok": policy_gate_ok,
        "policy_gate_violations": policy.get("violations") if isinstance(policy.get("violations"), list) else [],
        "provider_audit_rows": audit_rows,
        "provider_audit_available": provider_audit_available,
        "provider_audit_count": len(audit_rows),
        "repair_applied": bool(trace.get("repair_applied") or response_payload.get("repair_applied")),
        "codex_timeout": codex_timeout,
        "timeout": error_code in {"TimeoutError", "timeout", "codex_direct_timeout"} or codex_timeout,
        "effective_success": effective_success,
        "error_code": error_code or response_payload.get("error_code"),
        **phase_timings,
        **codex,
    }


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    success = [record for record in records if not record.get("error_code") and (record.get("http_status") in {None, 200} or record.get("mode") == "audit")]
    fallback_count = sum(1 for record in records if record.get("server_safe_fallback"))
    effective_success_count = sum(1 for record in records if record.get("effective_success"))
    temporarily_unavailable_count = sum(1 for record in records if record.get("temporarily_unavailable_leaked"))
    codex_timeout_count = sum(1 for record in records if record.get("codex_timeout"))
    provider_audit_missing_count = sum(1 for record in records if not record.get("provider_audit_available"))
    provider_audit_available_count = total - provider_audit_missing_count
    summary = {
        "generated_at": utc_now(),
        "label": records[0].get("label") if records else None,
        "sample_count": total,
        "success_count": len(success),
        "failure_count": total - len(success),
        "effective_success_count": effective_success_count,
        "effective_failure_count": total - effective_success_count,
        "effective_success_rate": round(effective_success_count / total, 6) if total else None,
        "timeout_count": sum(1 for record in records if record.get("timeout")),
        "codex_timeout_count": codex_timeout_count,
        "server_safe_fallback_count": fallback_count,
        "server_safe_fallback_rate": round(fallback_count / total, 6) if total else None,
        "temporarily_unavailable_count": temporarily_unavailable_count,
        "provider_audit_available": provider_audit_missing_count == 0 if total else None,
        "provider_audit_available_count": provider_audit_available_count,
        "provider_audit_missing_count": provider_audit_missing_count,
        "reply_raw_identifier_leak_count": sum(1 for record in records if record.get("reply_raw_identifier_leaked")),
        "payload_raw_identifier_leak_count": sum(1 for record in records if record.get("payload_raw_identifier_leaked")),
        "raw_identifier_leak_count": sum(1 for record in records if record.get("reply_raw_identifier_leaked")),
        "live_status_claim_count": sum(1 for record in records if record.get("live_status_claim_leaked")),
        "prompt_chars": metric(record.get("prompt_chars") or record.get("codex_prompt_chars") for record in records),
        "tracking_fact_ms": metric(record.get("tracking_fact_ms") for record in records),
        "runtime_context_ms": metric(record.get("runtime_context_ms") for record in records),
        "provider_ms": metric(record.get("provider_ms") for record in records),
        "policy_gate_ms": metric(record.get("policy_gate_ms") for record in records),
        "codex_total_ms": metric(record.get("codex_total_ms") or record.get("codex_latency_total_ms") or record.get("codex_elapsed_ms") for record in records),
        "subprocess_ms": metric(record.get("subprocess_ms") or record.get("codex_latency_subprocess_ms") for record in records),
        "readiness_ms": metric(record.get("readiness_ms") for record in records),
        "end_to_end_ms": metric(record.get("total_elapsed_ms") or record.get("elapsed_ms") for record in records),
    }
    return summary


def render_markdown(summary: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Codex Direct WebChat Latency",
        "",
        f"- Generated at: `{summary.get('generated_at')}`",
        f"- Samples: `{summary.get('sample_count')}`",
        f"- Successes: `{summary.get('success_count')}`",
        f"- Failures: `{summary.get('failure_count')}`",
        f"- Effective successes: `{summary.get('effective_success_count')}`",
        f"- Effective failures: `{summary.get('effective_failure_count')}`",
        f"- Effective success rate: `{summary.get('effective_success_rate')}`",
        f"- Timeouts: `{summary.get('timeout_count')}`",
        f"- Codex timeouts: `{summary.get('codex_timeout_count')}`",
        f"- Server safe fallback count: `{summary.get('server_safe_fallback_count')}`",
        f"- Server safe fallback rate: `{summary.get('server_safe_fallback_rate')}`",
        f"- Temporarily unavailable count: `{summary.get('temporarily_unavailable_count')}`",
        f"- Provider audit missing count: `{summary.get('provider_audit_missing_count')}`",
        f"- Raw identifier leaks: `{summary.get('raw_identifier_leak_count')}`",
        f"- Payload raw identifier leaks: `{summary.get('payload_raw_identifier_leak_count')}`",
        f"- Live status claim leaks: `{summary.get('live_status_claim_count')}`",
        "",
        "## Metrics",
        "",
        "| Metric | p50 | p95 | max |",
        "|---|---:|---:|---:|",
    ]
    for key in ("prompt_chars", "tracking_fact_ms", "runtime_context_ms", "provider_ms", "policy_gate_ms", "codex_total_ms", "subprocess_ms", "readiness_ms", "end_to_end_ms"):
        data = summary.get(key) if isinstance(summary.get(key), Mapping) else {}
        lines.append(f"| {key} | {data.get('p50')} | {data.get('p95')} | {data.get('max')} |")
    lines.extend(
        [
            "",
            "## Samples",
            "",
            "| index | session | http | effective | source | intent | fallback | temp_unavail | reply_leak | payload_leak | live_claim | audit | tracking_ms | provider_ms | prompt_chars | codex_ms | subprocess_ms | e2e_ms |",
            "|---:|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for record in records[:50]:
        lines.append(
            "| {index} | {session} | {http} | {effective} | {source} | {intent} | {fallback} | {temp} | {reply_leak} | {payload_leak} | {live} | {audit} | {tracking} | {provider} | {prompt} | {codex} | {subprocess} | {e2e} |".format(
                index=record.get("sample_index"),
                session=record.get("session_id"),
                http=record.get("http_status"),
                effective=record.get("effective_success"),
                source=record.get("reply_source"),
                intent=record.get("intent"),
                fallback=record.get("server_safe_fallback"),
                temp=record.get("temporarily_unavailable_leaked"),
                reply_leak=record.get("reply_raw_identifier_leaked"),
                payload_leak=record.get("payload_raw_identifier_leaked"),
                live=record.get("live_status_claim_leaked"),
                audit=record.get("provider_audit_available"),
                tracking=record.get("tracking_fact_ms"),
                provider=record.get("provider_ms"),
                prompt=record.get("prompt_chars") or record.get("codex_prompt_chars"),
                codex=record.get("codex_total_ms") or record.get("codex_latency_total_ms") or record.get("codex_elapsed_ms"),
                subprocess=record.get("subprocess_ms") or record.get("codex_latency_subprocess_ms"),
                e2e=record.get("total_elapsed_ms") or record.get("elapsed_ms"),
            )
        )
    lines.extend(
        [
            "",
            "## Scope Guard",
            "",
            "- Smoke mode calls only the configured WebChat Fast HTTP endpoint.",
            "- Audit mode reads provider runtime audit rows only.",
            "- The script does not print secrets, auth files, or provider tokens.",
            "- It does not change routing, Speedaf behavior, policy gates, or fallback configuration.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(args: argparse.Namespace, records: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> dict[str, str]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    label = safe_label(args.label)
    jsonl_path = output_dir / f"{label}.jsonl"
    summary_path = output_dir / f"{label}.summary.json"
    report_path = output_dir / f"{label}.md"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    report_path.write_text(render_markdown(summary, records), encoding="utf-8")
    return {"jsonl": str(jsonl_path), "summary": str(summary_path), "report": str(report_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Codex Direct WebChat latency")
    parser.add_argument("--mode", choices=["smoke", "audit"], default="smoke")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--body", default="CH1200000011425")
    parser.add_argument("--country-code", default="CH")
    parser.add_argument("--output-dir", default="artifacts/codex_direct_latency")
    parser.add_argument("--label", default=datetime.now(timezone.utc).strftime("codex_direct_webchat_%Y%m%d_%H%M%S"))
    parser.add_argument("--endpoint-url", default=DEFAULT_URL)
    parser.add_argument("--origin", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--tenant-key", default="default")
    parser.add_argument("--channel-key", default="website")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--since-minutes", type=int, default=None)
    parser.add_argument("--require-provider-audit", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be >= 1")
    if args.database_url is None:
        import os

        args.database_url = os.getenv("DATABASE_URL") or os.getenv("APP_DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")

    if args.mode == "audit":
        records = audit_records(args)
    else:
        records = [smoke_once(args, index) for index in range(args.iterations)]
    summary = summarize(records)
    paths = write_outputs(args, records, summary)
    print(json.dumps({"records": len(records), **paths}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
