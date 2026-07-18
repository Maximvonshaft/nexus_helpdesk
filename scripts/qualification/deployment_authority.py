#!/usr/bin/env python3
"""Static deployment-authority checks consumed by supply_chain.py only."""

from __future__ import annotations

from pathlib import Path

RETIRED_DEPLOY_PATHS = (
    "scripts/smoke/whatsapp_sidecar_candidate_smoke.sh",
    "docs/ops/NEXUS_NATIVE_WHATSAPP_CANDIDATE_SMOKE.md",
    "backend/tests/test_candidate_compose_contract.py",
)

TOMBSTONE_ENV_PATHS = (
    "deploy/.env.prod.example",
    "deploy/.env.prod.local-postgres.example",
    "deploy/.env.prod.external-postgres.example",
    "deploy/.env.candidate.example",
)


def deployment_authority_findings(root: Path) -> list[str]:
    findings: list[str] = []
    controlled = root / "deploy" / "docker-compose.controlled.yml"
    local_db = root / "deploy" / "docker-compose.controlled-postgres.yml"
    server_alias = root / "deploy" / "docker-compose.server.yml"
    candidate_alias = root / "deploy" / "docker-compose.candidate.yml"
    rollback = root / "scripts" / "deploy" / "rollback_release.sh"
    wrapper = root / "deploy" / "nexus-prod-compose.sh"

    for relative in RETIRED_DEPLOY_PATHS:
        if (root / relative).exists():
            findings.append(f"retired_deploy_path_exists:{relative}")

    for relative in TOMBSTONE_ENV_PATHS:
        path = root / relative
        if not path.is_file():
            findings.append(f"retired_env_tombstone_missing:{relative}")
            continue
        text = path.read_text(encoding="utf-8")
        if "NEXUS_ENV_TEMPLATE_RETIRED=true" not in text:
            findings.append(f"retired_env_not_tombstoned:{relative}")
        if len(text.splitlines()) > 20:
            findings.append(f"retired_env_tombstone_unbounded:{relative}")

    if controlled.is_file():
        text = controlled.read_text(encoding="utf-8")
        for forbidden in (
            "env_file:",
            "/run/secrets",
            "ai_runtime_token",
            "live_voice_token",
            "--queue all",
        ):
            if forbidden in text:
                findings.append(f"controlled_compose_forbidden:{forbidden}")

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

    if server_alias.is_file():
        content = server_alias.read_text(encoding="utf-8")
        if "services:" in content or "docker-compose.controlled.yml" not in content:
            findings.append("server_compose_not_thin_controlled_alias")
        if "docker-compose.controlled-postgres.yml" not in content:
            findings.append("server_compose_local_overlay_missing")

    if candidate_alias.is_file():
        content = candidate_alias.read_text(encoding="utf-8")
        if "services:" in content or "docker-compose.controlled.yml" not in content:
            findings.append("candidate_compose_not_thin_controlled_alias")

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

    if rollback.is_file():
        text = rollback.read_text(encoding="utf-8")
        for marker in (
            "ROLLBACK_CONTROLLED_ENV_FILE",
            "ROLLBACK_DATABASE_TOPOLOGY",
            "rollback_controlled_image_mismatch",
            "docker-compose.controlled.yml",
            "app-controlled",
        ):
            if marker not in text:
                findings.append(f"rollback_controlled_contract_missing:{marker}")
        for forbidden in (
            "COMPOSE_FILE=",
            "runtime-warmer",
            "IMAGE_TAG=\"$OLD_IMAGE_TAG\" docker compose",
        ):
            if forbidden in text:
                findings.append(f"rollback_legacy_path_present:{forbidden}")

    return findings
