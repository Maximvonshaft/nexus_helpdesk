#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.nexus_osr.runtime_evidence import (  # noqa: E402
    MAX_ARTIFACT_BYTES,
    ReadOnlyProbeSpec,
    bounded_json_bytes,
    build_runtime_evidence_snapshot,
    render_prometheus_metrics,
    run_read_only_http_probe,
)

MAX_INPUT_BYTES = 256 * 1024


def _load_json(path: Path) -> Any:
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ValueError(f"input_too_large:{path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _materialize_probes(
    *,
    config: Mapping[str, Any],
    fixture_payload: Any,
    tenant_id: str,
    staging_base_url: str | None,
    allowed_hosts: list[str],
    bearer_token: str | None,
) -> list[dict[str, Any]]:
    fixtures = fixture_payload if isinstance(fixture_payload, list) else []
    by_path = {
        str(item.get("path")): dict(item)
        for item in fixtures
        if isinstance(item, Mapping) and item.get("path")
    }
    results: list[dict[str, Any]] = []
    for raw_spec in config.get("probes") or []:
        if not isinstance(raw_spec, Mapping):
            continue
        path = str(raw_spec.get("path") or "")
        fixture = by_path.get(path)
        if fixture is not None:
            results.append(fixture)
            continue

        endpoint = raw_spec.get("endpoint")
        if staging_base_url and endpoint and bearer_token:
            results.append(
                run_read_only_http_probe(
                    ReadOnlyProbeSpec(
                        path=path,
                        endpoint=str(endpoint),
                        method=str(raw_spec.get("method") or "GET"),
                    ),
                    base_url=staging_base_url,
                    allowed_hosts=allowed_hosts,
                    tenant_id=tenant_id,
                    bearer_token=bearer_token,
                )
            )
            continue

        results.append(
            {
                "path": path,
                "method": str(raw_spec.get("method") or "GET"),
                "permission_granted": False,
                "status_code": 0,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "error_code": "source_unavailable",
            }
        )
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build bounded Nexus OSR runtime evidence from synthetic fixtures or explicitly allowed read-only staging GET probes."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-identity", type=Path, required=True)
    parser.add_argument("--observed-identity", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--probe-fixtures", type=Path, required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--now", help="ISO-8601 time for deterministic validation")
    parser.add_argument("--staging-base-url")
    parser.add_argument("--allow-host", action="append", default=[])
    parser.add_argument(
        "--admin-token-env",
        default="NEXUS_OSR_STAGING_ADMIN_TOKEN",
        help="Environment variable containing a staging-only read token. The value is never emitted.",
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Return success for degraded evidence; not_ready and unavailable always fail closed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = _load_json(args.config)
    expected_identity = _load_json(args.expected_identity)
    observed_identity = _load_json(args.observed_identity)
    samples = _load_json(args.samples)
    probe_fixtures = _load_json(args.probe_fixtures)
    if not isinstance(config, Mapping) or not isinstance(samples, Mapping):
        raise ValueError("invalid_runtime_evidence_input")

    bearer_token = os.environ.get(args.admin_token_env, "").strip() or None
    probes = _materialize_probes(
        config=config,
        fixture_payload=probe_fixtures,
        tenant_id=args.tenant,
        staging_base_url=args.staging_base_url,
        allowed_hosts=list(args.allow_host),
        bearer_token=bearer_token,
    )
    snapshot = build_runtime_evidence_snapshot(
        tenant_id=args.tenant,
        expected_identity=expected_identity,
        observed_identity=observed_identity,
        budget_definitions=list(config.get("failure_budgets") or []),
        samples=samples,
        probes=probes,
        now=_parse_now(args.now),
        max_age_seconds=int(config.get("max_evidence_age_seconds") or 900),
    )

    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    artifact_bytes = bounded_json_bytes(snapshot, max_bytes=MAX_ARTIFACT_BYTES)
    args.artifact.write_bytes(artifact_bytes + b"\n")
    args.metrics.write_text(render_prometheus_metrics(snapshot), encoding="utf-8")

    state = str(snapshot.get("state") or "unavailable")
    print(
        json.dumps(
            {
                "schema": snapshot.get("schema"),
                "state": state,
                "reason_codes": snapshot.get("reason_codes"),
                "artifact_bytes": len(artifact_bytes),
                "read_only": True,
            },
            sort_keys=True,
        )
    )
    if state in {"not_ready", "unavailable"}:
        return 2
    if state == "degraded" and not args.allow_degraded:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
