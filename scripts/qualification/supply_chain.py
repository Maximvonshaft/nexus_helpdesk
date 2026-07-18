#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qualification.deployment_authority import (  # noqa: E402
    deployment_authority_findings,
)

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
EXACT_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+(?:\s*;.*)?$"
)
EVIDENCE_DIR_ENV = "NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR"

SUPPLY_CHAIN_INPUTS = (
    "Dockerfile",
    "backend/requirements.txt",
    "webapp/package.json",
    "webapp/package-lock.json",
    "deploy/docker-compose.controlled.yml",
    "deploy/docker-compose.controlled-postgres.yml",
    "deploy/docker-compose.server.yml",
    "deploy/docker-compose.candidate.yml",
    "deploy/.env.controlled.example",
    "deploy/.env.controlled.local-postgres.example",
    "deploy/postgres/init-controlled-roles.sh",
    "deploy/nexus-prod-compose.sh",
    "scripts/deploy/validate_controlled_server_preflight.py",
    "scripts/deploy/safe_update_server.sh",
    "scripts/deploy/rollback_release.sh",
    "scripts/deploy/check_deploy_contract.sh",
    "scripts/validate_pr27_closure.sh",
    "scripts/verify_repository.py",
    "scripts/qualification/deployment_authority.py",
    "scripts/qualification/service_authority.py",
    "scripts/qualification/route_authority.py",
    "scripts/qualification/database_capacity.py",
    "scripts/qualification/infrastructure_decision.py",
    "scripts/qualification/local_storage_backup.py",
    "scripts/qualification/exact_head_acceptance.py",
    "scripts/qualification/postgres_acceptance.py",
    "scripts/release/assemble_supply_chain_evidence.py",
    "config/architecture/service-authority.v1.json",
    "config/architecture/compatibility-lifecycle.v1.json",
    "backend/tests/test_exact_head_acceptance.py",
    "docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md",
)

COMPOSE_INPUTS = (
    "deploy/docker-compose.controlled.yml",
    "deploy/docker-compose.controlled-postgres.yml",
    "deploy/docker-compose.server.yml",
    "deploy/docker-compose.candidate.yml",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dockerfile_findings(path: Path) -> list[str]:
    findings: list[str] = []
    content = path.read_text(encoding="utf-8")
    effective_content = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    for line in effective_content.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            image = stripped.split()[1]
            if not DIGEST_RE.search(image):
                findings.append(f"dockerfile_base_not_pinned:{image}")
    if re.search(r"\bapk\s+upgrade\b", effective_content):
        findings.append("dockerfile_mutable_apk_upgrade")
    if re.search(
        r"python\s+-m\s+pip\s+install\s+--upgrade\s+[^\\\n]*[><~=]",
        effective_content,
    ):
        findings.append("dockerfile_mutable_build_tool_range")
    return findings


def _requirements_findings(path: Path) -> list[str]:
    findings: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("--"):
            continue
        if not EXACT_REQUIREMENT_RE.match(stripped):
            findings.append(f"requirement_not_exact:{number}:{stripped[:120]}")
    return findings


def _compose_findings(path: Path) -> list[str]:
    findings: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("image:"):
            continue
        image = stripped.split(":", 1)[1].strip()
        if image.startswith("${"):
            continue
        if not DIGEST_RE.search(image):
            findings.append(
                f"compose_image_not_pinned:{path.name}:{number}:{image}"
            )
    return findings


def _load_json(
    path: Path,
    *,
    label: str,
    findings: list[str],
) -> dict[str, Any] | None:
    if path.is_symlink():
        findings.append(f"release_evidence_symlink_forbidden:{label}")
        return None
    if not path.is_file() or path.stat().st_size == 0:
        findings.append(f"release_evidence_missing:{label}")
        return None
    if path.stat().st_size > 64 * 1024 * 1024:
        findings.append(f"release_evidence_too_large:{label}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        findings.append(f"release_evidence_invalid_json:{label}")
        return None
    if not isinstance(payload, dict):
        findings.append(f"release_evidence_invalid_root:{label}")
        return None
    return payload


def _evidence_directory(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.expanduser().resolve()
    raw = os.getenv(EVIDENCE_DIR_ENV, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _inside_candidate_tree(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def collect_supply_chain_state(
    *,
    release: bool = False,
    evidence_dir: Path | None = None,
) -> dict[str, Any]:
    tracked = [ROOT / relative for relative in SUPPLY_CHAIN_INPUTS]
    findings: list[str] = []
    for path in tracked:
        if not path.is_file():
            findings.append(
                f"supply_chain_input_missing:{path.relative_to(ROOT)}"
            )

    dockerfile = ROOT / "Dockerfile"
    requirements = ROOT / "backend" / "requirements.txt"
    if dockerfile.is_file():
        findings.extend(_dockerfile_findings(dockerfile))
    if requirements.is_file():
        findings.extend(_requirements_findings(requirements))
    for relative in COMPOSE_INPUTS:
        path = ROOT / relative
        if path.is_file():
            findings.extend(_compose_findings(path))
    findings.extend(deployment_authority_findings(ROOT))

    evidence: dict[str, Any] = {
        "inputs": {
            str(path.relative_to(ROOT)): _sha256(path)
            for path in tracked
            if path.is_file()
        },
        "release_evidence_required": release,
        "candidate_tree_mutated": False,
    }

    if release:
        directory = _evidence_directory(evidence_dir)
        if directory is None:
            findings.append("release_evidence_dir_missing")
        elif _inside_candidate_tree(directory):
            findings.append("release_evidence_inside_candidate_tree")
        elif not directory.is_dir() or directory.is_symlink():
            findings.append("release_evidence_dir_invalid")
        else:
            required = {
                "sbom": directory / "sbom.spdx.json",
                "provenance": directory / "provenance.json",
                "signature_bundle": directory / "cosign.bundle.json",
            }
            loaded = {
                name: _load_json(path, label=name, findings=findings)
                for name, path in required.items()
            }
            sbom = loaded["sbom"]
            provenance = loaded["provenance"]
            signature = loaded["signature_bundle"]
            if sbom is not None and not str(
                sbom.get("spdxVersion") or ""
            ).startswith("SPDX-"):
                findings.append("release_evidence_invalid_spdx")
            if provenance is not None:
                if provenance.get("_type") != "https://in-toto.io/Statement/v1":
                    findings.append("release_evidence_invalid_provenance_type")
                if not isinstance(provenance.get("subject"), list) or not provenance.get(
                    "subject"
                ):
                    findings.append("release_evidence_missing_provenance_subject")
            if signature is not None and not signature:
                findings.append("release_evidence_empty_signature_bundle")
            for name, path in required.items():
                if path.is_file() and not path.is_symlink() and path.stat().st_size > 0:
                    evidence[name] = {
                        "path": str(path),
                        "sha256": _sha256(path),
                    }
            evidence["evidence_dir"] = str(directory)

    return {
        "schema": "nexus.supply-chain-qualification.v1",
        "status": "pass" if not findings else "fail",
        "findings": findings,
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = collect_supply_chain_state(
        release=args.release,
        evidence_dir=args.evidence_dir,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        if _inside_candidate_tree(output):
            raise SystemExit("supply-chain output must remain outside candidate tree")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
