#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from dependency_lock import dependency_lock_findings
from deployment_authority import deployment_authority_findings
from image_runtime_contract import image_runtime_contract_findings
from secret_strategy import secret_strategy_findings

ROOT = Path(__file__).resolve().parents[2]
TRACKED_INPUTS = (
    "Dockerfile",
    "backend/requirements.txt",
    "webapp/package-lock.json",
    "deploy/docker-compose.controlled.yml",
    "deploy/docker-compose.controlled-postgres.yml",
    "deploy/nexus-prod-compose.sh",
    "scripts/deploy/rollback_release.sh",
    "scripts/release/run_controlled_image_assurance.sh",
    "scripts/release/assemble_supply_chain_evidence.py",
    "scripts/security/scan_repository.py",
    "config/security/secret-scan-allowlist.json",
    "config/security/codeql-exceptions.json",
)
COMPOSE_INPUTS = (
    "deploy/docker-compose.controlled.yml",
    "deploy/docker-compose.controlled-postgres.yml",
)
EXPECTED_PYTHON_DIRECT = (
    "alembic",
    "bcrypt",
    "fastapi",
    "gunicorn",
    "httpx",
    "livekit-api",
    "openpyxl",
    "passlib",
    "prometheus-client",
    "psycopg",
    "pydantic",
    "pydantic-settings",
    "PyJWT",
    "python-multipart",
    "redis",
    "requests",
    "SQLAlchemy",
    "uvicorn",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FROM_IMAGE = re.compile(r"^FROM\s+([^\s]+)", re.IGNORECASE)
COMPOSE_IMAGE = re.compile(r"^\s*image:\s*(.+?)\s*$")


def _inside_candidate_tree(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(
    path: Path,
    *,
    label: str,
    findings: list[str],
) -> dict[str, Any] | None:
    if not path.is_file():
        findings.append(f"release_evidence_missing:{label}")
        return None
    if path.is_symlink():
        findings.append(f"release_evidence_symlink_forbidden:{label}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        findings.append(f"release_evidence_invalid_json:{label}")
        return None
    if not isinstance(payload, dict):
        findings.append(f"release_evidence_not_object:{label}")
        return None
    return payload


def _requirement_findings(path: Path) -> list[str]:
    findings: list[str] = []
    names: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("--"):
            continue
        if "==" not in stripped:
            findings.append(f"python_requirement_not_exact:{number}")
            continue
        requirement = stripped.split(";", 1)[0]
        name, version = requirement.split("==", 1)
        normalized = name.split("[", 1)[0].strip()
        names.append(normalized)
        if not version.strip():
            findings.append(f"python_requirement_version_missing:{number}")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        findings.append(f"python_requirement_duplicate:{','.join(duplicates)}")
    if tuple(names) != EXPECTED_PYTHON_DIRECT:
        findings.append(
            "python_direct_dependency_set_mismatch:"
            f"expected={list(EXPECTED_PYTHON_DIRECT)}:actual={names}"
        )
    return findings


def _dockerfile_findings(path: Path) -> list[str]:
    findings: list[str] = []
    source = path.read_text(encoding="utf-8")
    from_lines: list[str] = []
    for line in source.splitlines():
        match = FROM_IMAGE.match(line.strip())
        if not match:
            continue
        image = match.group(1)
        from_lines.append(image)
        if "@sha256:" not in image:
            findings.append(f"docker_base_image_not_digest_pinned:{image}")
    if not from_lines:
        findings.append("dockerfile_base_image_missing")
    if re.search(r"\bapk\s+upgrade\b", source):
        findings.append("dockerfile_mutable_apk_upgrade_forbidden")
    if "npm ci --ignore-scripts" not in source:
        findings.append("dockerfile_frontend_install_not_deterministic")
    for marker in (
        "requirements.txt",
        "--no-cache-dir",
        "--require-hashes",
        "USER nexus",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.created",
        "org.opencontainers.image.version",
        "org.opencontainers.image.ref.name",
        "GIT_SHA",
        "BUILD_TIME",
        "IMAGE_TAG",
    ):
        if marker not in source:
            findings.append(f"dockerfile_supply_marker_missing:{marker}")
    return findings


def _lock_findings(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["frontend_lock_invalid"]
    if payload.get("lockfileVersion") != 3:
        findings.append("frontend_lock_version_not_3")
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        return [*findings, "frontend_lock_packages_missing"]
    integrity_missing: list[str] = []
    for name, entry in packages.items():
        if not name.startswith("node_modules/") or not isinstance(entry, dict):
            continue
        if not entry.get("resolved") or not entry.get("integrity"):
            integrity_missing.append(name)
    if integrity_missing:
        findings.append(
            "frontend_lock_integrity_missing:"
            f"{','.join(sorted(integrity_missing)[:20])}"
        )
    return findings


def _compose_findings(path: Path) -> list[str]:
    findings: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = COMPOSE_IMAGE.match(line)
        if not match:
            continue
        value = match.group(1).strip().strip('"\'')
        if value.startswith("${"):
            if ":?" not in value:
                findings.append(f"compose_image_not_required:{path.name}:{value}")
            if "CONTROLLED_IMAGE" not in value:
                findings.append(f"compose_noncanonical_image_variable:{path.name}:{value}")
            continue
        if "@sha256:" not in value:
            findings.append(f"compose_image_not_digest_pinned:{path.name}:{value}")
    return findings


def _evidence_directory(value: Path | None) -> Path | None:
    return value.expanduser().resolve() if value is not None else None


def collect_supply_chain_state(
    *,
    release: bool,
    evidence_dir: Path | None = None,
) -> dict[str, Any]:
    findings: list[str] = []
    tracked: list[Path] = []
    for relative in TRACKED_INPUTS:
        path = ROOT / relative
        if not path.is_file():
            findings.append(f"supply_input_missing:{relative}")
            continue
        tracked.append(path)

    findings.extend(_dockerfile_findings(ROOT / "Dockerfile"))
    findings.extend(_requirement_findings(ROOT / "backend/requirements.txt"))
    findings.extend(_lock_findings(ROOT / "webapp/package-lock.json"))
    findings.extend(dependency_lock_findings(ROOT))
    findings.extend(secret_strategy_findings(ROOT))
    findings.extend(image_runtime_contract_findings(ROOT))
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


def _default_ci_output() -> Path | None:
    if os.getenv("CI", "").strip().lower() != "true":
        return None
    directory = Path("/tmp/nexus-static")
    return directory / "supply-chain.json" if directory.is_dir() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--output", type=Path, default=_default_ci_output())
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
