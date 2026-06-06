#!/usr/bin/env python3
"""Codex Direct latency baseline probe.

This script is intentionally read-only for production business data by default.

Supported modes:
- audit: read provider_runtime_audit_logs and generate a JSONL/SLO report.
- direct: call the CodexDirectAdapter with synthetic ProviderRequest payloads.
          This does not create tickets, conversations, messages, or Speedaf writes.

The script is designed for PR #406 baseline work after PR #404/#405 landed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast


LATENCY_KEYS = (
    "readiness_ms",
    "login_status_ms",
    "prompt_build_ms",
    "argv_build_ms",
    "subprocess_ms",
    "parse_ms",
    "total_ms",
)

NUMERIC_FIELDS = (
    "speedaf_tool_ms",
    "codex_readiness_ms",
    "codex_login_status_ms",
    "codex_prompt_build_ms",
    "codex_argv_build_ms",
    "codex_subprocess_ms",
    "codex_parse_ms",
    "codex_total_ms",
    "provider_elapsed_ms",
    "e2e_ms",
    "prompt_chars",
    "stdout_chars",
    "stderr_chars",
)

FAILOVER_WORTHY_ERRORS = {
    "codex_direct_timeout",
    "codex_direct_nonzero_exit",
    "codex_direct_empty_reply",
    "codex_direct_bad_json",
    "parse_reject",
}


@dataclass(frozen=True)
class SummaryMetric:
    count: int
    p50: float | None
    p95: float | None
    minimum: float | None
    maximum: float | None
    mean: float | None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return {"_unparsed_safe_summary": stripped[:1000]}
        if isinstance(decoded, dict):
            return decoded
    return {}


def nested_get(mapping: Mapping[str, Any], path: Sequence[str], default: Any = None) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(float(stripped))
        except ValueError:
            return None
    return None


def bool_from_status(status: Any, error_code: Any) -> bool:
    normalized = str(status or "").strip().lower()
    if normalized in {"ok", "success", "succeeded"}:
        return True
    if normalized in {"failed", "error", "skipped"}:
        return False
    return not bool(error_code)


def extract_speedaf_tool_ms(summary: Mapping[str, Any]) -> int | None:
    candidates = [
        summary.get("speedaf_tool_ms"),
        summary.get("tracking_fact_latency_ms"),
        summary.get("tracking_lookup_ms"),
        nested_get(summary, ["tool_latency", "speedaf.order.query"]),
        nested_get(summary, ["tool_latency_ms", "speedaf.order.query"]),
        nested_get(summary, ["tracking_fact", "latency_ms"]),
        nested_get(summary, ["tracking_fact_metadata", "latency_ms"]),
    ]
    for candidate in candidates:
        parsed = as_int(candidate)
        if parsed is not None:
            return parsed
    return None


def normalize_record(
    *,
    source: str,
    benchmark_profile: str,
    sample_index: int | None,
    request_id: str | None,
    session_id: str | None,
    provider: str,
    status: str,
    error_code: str | None,
    elapsed_ms: int | None,
    safe_summary: Mapping[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    latency = parse_json_dict(safe_summary.get("latency"))
    if not latency and isinstance(safe_summary.get("codex_direct"), Mapping):
        latency = parse_json_dict(nested_get(safe_summary, ["codex_direct", "latency"]))

    record: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "timestamp": created_at or utc_now_iso(),
        "source": source,
        "benchmark_profile": benchmark_profile,
        "sample_index": sample_index,
        "request_id": request_id,
        "session_id": session_id,
        "provider": provider,
        "status": status,
        "ok": bool_from_status(status, error_code),
        "error_code": error_code,
        "reply_source": safe_summary.get("reply_source") or provider,
        "provider_elapsed_ms": as_int(elapsed_ms),
        "speedaf_tool_ms": extract_speedaf_tool_ms(safe_summary),
        "prompt_chars": as_int(safe_summary.get("prompt_chars")),
        "stdout_chars": as_int(safe_summary.get("stdout_chars")),
        "stderr_chars": as_int(safe_summary.get("stderr_chars")),
        "timeout_seconds": as_int(safe_summary.get("timeout_seconds")),
        "timeout_source": safe_summary.get("timeout_source"),
        "timeout_hit": error_code == "codex_direct_timeout" or bool(safe_summary.get("timeout_source")),
        "failover_worthy": error_code in FAILOVER_WORTHY_ERRORS,
        "e2e_ms": as_int(
            safe_summary.get("e2e_ms")
            or safe_summary.get("end_to_end_ms")
            or safe_summary.get("webchat_fast_total_ms")
        ),
    }

    for key in LATENCY_KEYS:
        record[f"codex_{key}"] = as_int(latency.get(key))

    if record["codex_total_ms"] is None:
        record["codex_total_ms"] = record["provider_elapsed_ms"]

    return record


def normalize_audit_row(row: Mapping[str, Any], *, benchmark_profile: str) -> dict[str, Any]:
    summary = parse_json_dict(row.get("safe_summary"))
    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        created = created_at.astimezone(timezone.utc).isoformat()
    else:
        created = str(created_at) if created_at is not None else None

    return normalize_record(
        source="provider_runtime_audit_logs",
        benchmark_profile=benchmark_profile,
        sample_index=None,
        request_id=str(row.get("request_id") or "") or None,
        session_id=str(row.get("session_id") or "") or None,
        provider=str(row.get("provider") or "codex_direct"),
        status=str(row.get("status") or ""),
        error_code=str(row.get("error_code") or "") or None,
        elapsed_ms=as_int(row.get("elapsed_ms")),
        safe_summary=summary,
        created_at=created,
    )


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
        return clean[int(position)]
    lower_value = clean[lower]
    upper_value = clean[upper]
    return lower_value + (upper_value - lower_value) * (position - lower)


def summarize_metric(values: Iterable[Any]) -> SummaryMetric:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return SummaryMetric(count=0, p50=None, p95=None, minimum=None, maximum=None, mean=None)
    return SummaryMetric(
        count=len(clean),
        p50=percentile(clean, 0.50),
        p95=percentile(clean, 0.95),
        minimum=min(clean),
        maximum=max(clean),
        mean=statistics.fmean(clean),
    )


def summarize_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    failures = [record for record in records if not record.get("ok")]
    timeouts = [record for record in records if record.get("timeout_hit")]
    failover_worthy = [record for record in records if record.get("failover_worthy")]

    metrics = {
        field: summarize_metric(record.get(field) for record in records).__dict__
        for field in NUMERIC_FIELDS
    }
    error_counts: dict[str, int] = {}
    for record in records:
        error_code = record.get("error_code")
        if error_code:
            error_counts[str(error_code)] = error_counts.get(str(error_code), 0) + 1

    profiles: dict[str, dict[str, Any]] = {}
    for record in records:
        profile = str(record.get("benchmark_profile") or "unknown")
        profiles.setdefault(profile, {"count": 0, "timeouts": 0, "failures": 0})
        profiles[profile]["count"] += 1
        profiles[profile]["timeouts"] += int(bool(record.get("timeout_hit")))
        profiles[profile]["failures"] += int(not record.get("ok"))

    recommendation = recommend_next_step(metrics=metrics, timeout_rate=(len(timeouts) / total if total else None))

    return {
        "generated_at": utc_now_iso(),
        "sample_count": total,
        "failure_count": len(failures),
        "timeout_count": len(timeouts),
        "failover_worthy_count": len(failover_worthy),
        "failure_rate": round(len(failures) / total, 6) if total else None,
        "timeout_rate": round(len(timeouts) / total, 6) if total else None,
        "metrics": metrics,
        "error_counts": error_counts,
        "profiles": profiles,
        "recommendation": recommendation,
    }


def recommend_next_step(*, metrics: Mapping[str, Any], timeout_rate: float | None) -> str:
    codex_total = metrics.get("codex_total_ms") or {}
    p95 = codex_total.get("p95")
    if p95 is None:
        return "insufficient_data"
    if timeout_rate is not None and timeout_rate >= 0.02:
        return "introduce_health_aware_ai_fallback_and_shadow_worker"
    if p95 < 8000:
        return "proceed_to_prompt_compression"
    if p95 <= 15000:
        return "proceed_to_prompt_compression_plus_warmup_health_probe"
    return "prioritize_ai_fallback_provider_and_codex_worker_shadow_test"


def fmt_ms(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f} ms"


def fmt_rate(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def render_markdown_report(summary: Mapping[str, Any], records: Sequence[Mapping[str, Any]], *, title: str) -> str:
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), Mapping) else {}
    lines = [
        f"# {title}",
        "",
        f"- Generated at: `{summary.get('generated_at')}`",
        f"- Samples: `{summary.get('sample_count')}`",
        f"- Failure rate: `{fmt_rate(summary.get('failure_rate'))}`",
        f"- Timeout rate: `{fmt_rate(summary.get('timeout_rate'))}`",
        f"- Failover-worthy failures: `{summary.get('failover_worthy_count')}`",
        f"- Recommendation: `{summary.get('recommendation')}`",
        "",
        "## Metric summary",
        "",
        "| Metric | Count | p50 | p95 | min | max | mean |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for field in NUMERIC_FIELDS:
        metric = metrics.get(field, {}) if isinstance(metrics, Mapping) else {}
        lines.append(
            "| {field} | {count} | {p50} | {p95} | {minimum} | {maximum} | {mean} |".format(
                field=field,
                count=metric.get("count", 0),
                p50=fmt_ms(metric.get("p50")) if field.endswith("_ms") else metric.get("p50", "n/a"),
                p95=fmt_ms(metric.get("p95")) if field.endswith("_ms") else metric.get("p95", "n/a"),
                minimum=fmt_ms(metric.get("minimum")) if field.endswith("_ms") else metric.get("minimum", "n/a"),
                maximum=fmt_ms(metric.get("maximum")) if field.endswith("_ms") else metric.get("maximum", "n/a"),
                mean=fmt_ms(metric.get("mean")) if field.endswith("_ms") else metric.get("mean", "n/a"),
            )
        )

    error_counts = summary.get("error_counts") or {}
    lines.extend(["", "## Error counts", ""])
    if error_counts:
        lines.extend([f"- `{key}`: {value}" for key, value in sorted(error_counts.items())])
    else:
        lines.append("- none")

    profiles = summary.get("profiles") or {}
    lines.extend(["", "## Profiles", ""])
    if profiles:
        lines.extend(
            f"- `{name}`: count={data.get('count')}, failures={data.get('failures')}, timeouts={data.get('timeouts')}"
            for name, data in sorted(profiles.items())
            if isinstance(data, Mapping)
        )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Scope guard",
            "",
            "- This report is generated from Codex Direct telemetry or synthetic direct adapter probes.",
            "- It does not enable canned deterministic replies as the main path.",
            "- It does not change Speedaf API behavior, tracking source, or WebChat production routing.",
            "- Direct mode uses synthetic ProviderRequest payloads and does not create tickets, conversations, customer messages, or Speedaf write actions.",
            "",
            "## Recent samples",
            "",
            "| timestamp | profile | status | error_code | total_ms | subprocess_ms | prompt_chars | stdout_chars | stderr_chars |",
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for record in records[:20]:
        lines.append(
            "| {timestamp} | {profile} | {status} | {error} | {total} | {subprocess} | {prompt} | {stdout} | {stderr} |".format(
                timestamp=record.get("timestamp"),
                profile=record.get("benchmark_profile"),
                status=record.get("status"),
                error=record.get("error_code") or "",
                total=record.get("codex_total_ms"),
                subprocess=record.get("codex_subprocess_ms"),
                prompt=record.get("prompt_chars"),
                stdout=record.get("stdout_chars"),
                stderr=record.get("stderr_chars"),
            )
        )

    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            decoded = json.loads(stripped)
            if isinstance(decoded, dict):
                records.append(decoded)
    return records


def query_audit_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    database_url = args.database_url or os.getenv("DATABASE_URL") or os.getenv("APP_DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL, APP_DATABASE_URL, or --database-url is required for audit mode")

    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    cutoff = None
    if args.since_minutes:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes)

    where = ["provider = :provider"]
    params: dict[str, Any] = {"provider": args.provider, "limit": args.limit}
    if cutoff is not None:
        where.append("created_at >= :cutoff")
        params["cutoff"] = cutoff
    if args.operation:
        where.append("operation = :operation")
        params["operation"] = args.operation

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

    return [normalize_audit_row(row, benchmark_profile=args.profile_label) for row in rows]


def build_synthetic_request(sample_index: int, args: argparse.Namespace) -> Any:
    from app.services.provider_runtime.schemas import ProviderRequest

    body_seed = args.customer_message or "Where is my parcel? Please check tracking number SFTEST000000001."
    if args.prompt_size_chars and args.prompt_size_chars > len(body_seed):
        body = (body_seed + " ") * max(1, (args.prompt_size_chars // max(1, len(body_seed))) + 1)
        body = body[: args.prompt_size_chars]
    else:
        body = body_seed

    tracking_fact_summary = None
    tracking_fact_evidence_present = bool(args.tracking_fact_present)
    if tracking_fact_evidence_present:
        tracking_fact_summary = (
            "Trusted Speedaf tracking fact: waybill_no=SFTEST000000001; "
            "current_status=In transit; latest_event=Arrived at sorting center; "
            "status_time=2026-06-07T08:00:00Z. This is synthetic benchmark evidence."
        )

    recent_context = []
    if args.include_recent_context:
        recent_context = [
            {"role": "user", "content": "Previous benchmark customer message."},
            {"role": "assistant", "content": "Previous benchmark assistant reply."},
        ]

    metadata = {
        "knowledge_context": {"retrieval": "benchmark_synthetic", "hits": []},
        "persona_context": {
            "profile_key": "benchmark.website.en",
            "name": "Benchmark WebChat",
            "summary": "Concise customer service replies.",
        },
        "benchmark": {
            "synthetic": True,
            "profile_label": args.profile_label,
            "sample_index": sample_index,
        },
    }

    return ProviderRequest(
        request_id=f"bench-codex-direct-{uuid.uuid4()}",
        tenant_id=args.tenant_id,
        tenant_key=args.tenant_id,
        channel_key=args.channel_key,
        session_id=f"bench-session-{args.profile_label}",
        scenario="webchat_fast_reply",
        body=body,
        recent_context=recent_context,
        tracking_fact_summary=tracking_fact_summary,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=args.timeout_ms,
        metadata=metadata,
    )


async def run_direct_once(sample_index: int, args: argparse.Namespace) -> dict[str, Any]:
    from app.services.provider_runtime.adapters.codex_direct import CodexDirectAdapter

    adapter = CodexDirectAdapter()
    request = build_synthetic_request(sample_index, args)
    result = await adapter.generate(cast(Any, None), request)
    status = "ok" if result.ok else "failed"
    return normalize_record(
        source="codex_direct_adapter_synthetic_probe",
        benchmark_profile=args.profile_label,
        sample_index=sample_index,
        request_id=request.request_id,
        session_id=request.session_id,
        provider=result.provider,
        status=status,
        error_code=result.error_code,
        elapsed_ms=result.elapsed_ms,
        safe_summary=parse_json_dict(result.raw_payload_safe_summary),
    )


async def run_direct_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.warmup_probe:
        from app.services.provider_runtime.adapters.codex_direct import CodexDirectAdapter

        await CodexDirectAdapter().readiness_check()

    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def guarded(index: int) -> dict[str, Any]:
        async with semaphore:
            return await run_direct_once(index, args)

    if args.concurrency <= 1:
        return [await guarded(index) for index in range(args.runs)]
    return list(await asyncio.gather(*(guarded(index) for index in range(args.runs))))


def output_paths(output_dir: Path, label: str) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label)[:80] or "codex_baseline"
    return (
        output_dir / f"{safe_label}.jsonl",
        output_dir / f"{safe_label}.summary.json",
        output_dir / f"{safe_label}.slo.md",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex Direct latency baseline probe")
    parser.add_argument("--mode", choices=["audit", "direct", "jsonl"], default="audit")
    parser.add_argument("--output-dir", default="artifacts/codex_direct_latency")
    parser.add_argument("--label", default=datetime.now(timezone.utc).strftime("codex_latency_%Y%m%d_%H%M%S"))
    parser.add_argument("--profile-label", default="observed_audit")
    parser.add_argument("--provider", default="codex_direct")
    parser.add_argument("--operation", default="generate")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--since-minutes", type=int, default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--input-jsonl", default=None)

    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout-ms", type=int, default=26000)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--channel-key", default="website")
    parser.add_argument("--customer-message", default=None)
    parser.add_argument("--prompt-size-chars", type=int, default=0)
    parser.add_argument("--tracking-fact-present", action="store_true")
    parser.add_argument("--include-recent-context", action="store_true")
    parser.add_argument("--warmup-probe", action="store_true")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")

    if args.mode == "audit":
        records = query_audit_records(args)
    elif args.mode == "jsonl":
        if not args.input_jsonl:
            raise SystemExit("--input-jsonl is required for jsonl mode")
        records = read_jsonl(Path(args.input_jsonl))
    else:
        records = await run_direct_records(args)

    summary = summarize_records(records)
    jsonl_path, summary_path, report_path = output_paths(Path(args.output_dir), args.label)
    write_jsonl(jsonl_path, records)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    report_path.write_text(render_markdown_report(summary, records, title="Codex Direct Latency Baseline"), encoding="utf-8")

    print(json.dumps({
        "records": len(records),
        "jsonl": str(jsonl_path),
        "summary": str(summary_path),
        "report": str(report_path),
        "recommendation": summary.get("recommendation"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
