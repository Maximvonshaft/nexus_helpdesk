#!/usr/bin/env python3
"""Static deployment-authority checks consumed by supply_chain.py."""

from __future__ import annotations

import json
import re
from pathlib import Path

RETIRED_DEPLOY_PATHS = (
    "deploy/docker-compose.server.yml",
    "deploy/docker-compose.candidate.yml",
    "deploy/.env.prod.example",
    "deploy/.env.prod.local-postgres.example",
    "deploy/.env.prod.external-postgres.example",
    "deploy/.env.candidate.example",
    "deploy/systemd/nexusdesk-worker.service",
    "backend/scripts/run_api_manual.py",
    "backend/scripts/run_worker_manual.py",
    "scripts/smoke/whatsapp_sidecar_candidate_smoke.sh",
    "docs/ops/NEXUS_NATIVE_WHATSAPP_CANDIDATE_SMOKE.md",
    "backend/tests/test_candidate_compose_contract.py",
    "scripts/deploy/prepare_production_release_env.sh",
    "backend/tests/test_release_metadata_security_contract.py",
    "scripts/smoke/worker_daemon_readiness_probe.py",
    "backend/tests/test_worker_daemon_readiness.py",
    "docs/deployment/release-metadata.md",
    "docs/deploy-server-local-postgres.md",
    "docs/deploy-server-external-postgres.md",
)

DEPLOYMENT_TEXT_SUFFIXES = {
    ".yml",
    ".yaml",
    ".sh",
    ".service",
    ".conf",
    ".template",
    ".env",
    ".example",
}

CURRENT_WORKER_SERVICES = (
    "worker-outbound-controlled",
    "worker-background-controlled",
    "worker-webchat-ai-controlled",
)

OPERATIONAL_AUTHORITY_PATHS = (
    "README.md",
    "docs/runbook-production.md",
    "docs/deployment-runbook.md",
    "docs/ops/alerting.md",
    "docs/performance-budgets.md",
    "docs/runbooks/production-activation.md",
    "docs/runbooks/release-metadata-consistency-gate.md",
    "scripts/probe_nexus_runtime.sh",
    "scripts/smoke/runtime_performance_baseline.sh",
    "scripts/deploy/rollback_release.sh",
    "scripts/release_metadata_consistency_gate.py",
)

OPERATIONAL_WORKER_PATHS = (
    "docs/runbooks/production-activation.md",
    "scripts/probe_nexus_runtime.sh",
    "scripts/smoke/runtime_performance_baseline.sh",
    "scripts/deploy/rollback_release.sh",
)

RETIRED_OPERATIONAL_MARKERS = (
    "worker-handoff-snapshot-controlled",
    "worker-handoff-snapshot",
    "handoff-snapshot",
    "deploy/docker-compose.server.yml",
    "deploy/.env.prod",
    "deploy-app-1",
    "WEBCHAT_VOICE_ENABLED",
    "WHATSAPP_NATIVE_ENABLED",
    "WHATSAPP_DISPATCH_MODE",
    "http://127.0.0.1:18081",
)


def _deployment_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in (root / "deploy", root / "scripts/deploy"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and (
                path.suffix.lower() in DEPLOYMENT_TEXT_SUFFIXES
                or path.name.endswith(".example")
            ):
                paths.append(path)
    return sorted(set(paths))


def _operational_authority_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for relative in OPERATIONAL_AUTHORITY_PATHS:
        path = root / relative
        if not path.is_file():
            findings.append(f"operational_authority_missing:{relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in RETIRED_OPERATIONAL_MARKERS:
            if marker in text:
                findings.append(
                    f"retired_operational_marker:{relative}:{marker}"
                )

    for relative in OPERATIONAL_WORKER_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for service in CURRENT_WORKER_SERVICES:
            if service not in text:
                findings.append(
                    f"operational_worker_missing:{relative}:{service}"
                )

    probe = root / "scripts/probe_nexus_runtime.sh"
    if probe.is_file():
        text = probe.read_text(encoding="utf-8", errors="replace")
        for marker in (
            "X-Metrics-Token",
            "metrics endpoint accepted an unauthenticated request",
            "metrics authenticated probe failed",
        ):
            if marker not in text:
                findings.append(f"runtime_probe_contract_missing:{marker}")

    activation = root / "docs/runbooks/production-activation.md"
    if activation.is_file():
        text = activation.read_text(encoding="utf-8", errors="replace")
        for marker in (
            "deploy/nexus-prod-compose.sh",
            "NEXUS_DATABASE_TOPOLOGY",
            "NEXUS_CONTROLLED_ENV_FILE",
        ):
            if marker not in text:
                findings.append(
                    f"production_activation_authority_missing:{marker}"
                )

    release_gate = root / "scripts/release_metadata_consistency_gate.py"
    if release_gate.is_file():
        text = release_gate.read_text(encoding="utf-8", errors="replace")
        if 'parser.add_argument("--container", default="")' not in text:
            findings.append("release_gate_container_must_not_be_guessed")
        if "container_required" not in text:
            findings.append("release_gate_container_requirement_missing")

    return findings


def deployment_authority_findings(root: Path) -> list[str]:
    findings: list[str] = []
    controlled = root / "deploy/docker-compose.controlled.yml"
    local_db = root / "deploy/docker-compose.controlled-postgres.yml"
    rollback = root / "scripts/deploy/rollback_release.sh"
    wrapper = root / "deploy/nexus-prod-compose.sh"

    for relative in RETIRED_DEPLOY_PATHS:
        if (root / relative).exists():
            findings.append(f"retired_deploy_path_exists:{relative}")

    for path in _deployment_files(root):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?:python\s+)?scripts/run_worker\.py\b", text):
            findings.append(f"unsupervised_worker_entrypoint:{relative}")
        if "--queue all" in text:
            findings.append(f"queue_all_worker_forbidden:{relative}")
        if ".env.local-manual" in text:
            findings.append(f"manual_environment_bypass:{relative}")

    if controlled.is_file():
        text = controlled.read_text(encoding="utf-8")
        for forbidden in (
            "env_file:",
            "/run/secrets",
            "ai_runtime_token",
            "live_voice_token",
            "--queue all",
            "/proc/1/cmdline",
            "controlled-worker-ok",
            "scripts/run_worker.py",
            "worker-handoff-snapshot-controlled",
            "handoff-snapshot",
        ):
            if forbidden in text:
                findings.append(f"controlled_compose_forbidden:{forbidden}")
        for required in (
            "run_worker_supervised.py",
            "scripts/check_worker_progress.py",
            "NEXUS_WORKER_ID",
            "NEXUS_WORKER_QUEUE",
            *CURRENT_WORKER_SERVICES,
        ):
            if required not in text:
                findings.append(f"controlled_compose_required_missing:{required}")

    if local_db.is_file():
        text = local_db.read_text(encoding="utf-8")
        if "postgres-controlled:" not in text:
            findings.append("controlled_postgres_service_missing")
        for forbidden in (
            "app-controlled:",
            "worker-outbound-controlled:",
            "worker-background-controlled:",
            "worker-webchat-ai-controlled:",
            "worker-handoff-snapshot-controlled:",
        ):
            if forbidden in text:
                findings.append(
                    f"controlled_postgres_duplicates_service:{forbidden}"
                )

    if wrapper.is_file():
        text = wrapper.read_text(encoding="utf-8")
        for marker in (
            "NEXUS_DATABASE_TOPOLOGY",
            "docker-compose.controlled.yml",
            "docker-compose.controlled-postgres.yml",
            "--env-file",
        ):
            if marker not in text:
                findings.append(f"production_wrapper_contract_missing:{marker}")
        for forbidden in (
            "docker-compose.server.yml",
            "docker-compose.candidate.yml",
            ".env.prod",
            ".env.candidate",
        ):
            if forbidden in text:
                findings.append(
                    f"production_wrapper_legacy_reference:{forbidden}"
                )

    if rollback.is_file():
        text = rollback.read_text(encoding="utf-8")
        for marker in (
            "ROLLBACK_CONTROLLED_ENV_FILE",
            "ROLLBACK_DATABASE_TOPOLOGY",
            "rollback_controlled_image_mismatch",
            "docker-compose.controlled.yml",
            "app-controlled",
            *CURRENT_WORKER_SERVICES,
        ):
            if marker not in text:
                findings.append(f"rollback_controlled_contract_missing:{marker}")
        for forbidden in (
            "COMPOSE_FILE=",
            "runtime-warmer",
            'IMAGE_TAG="$OLD_IMAGE_TAG" docker compose',
            "docker-compose.server.yml",
            "docker-compose.candidate.yml",
            "worker-handoff-snapshot-controlled",
        ):
            if forbidden in text:
                findings.append(f"rollback_legacy_path_present:{forbidden}")

    findings.extend(_operational_authority_findings(root))
    return sorted(set(findings))


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    findings = deployment_authority_findings(root)
    payload = {
        "schema": "nexus.deployment-authority.v3",
        "status": "pass" if not findings else "fail",
        "findings": findings,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
