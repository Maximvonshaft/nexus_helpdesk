#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
EXACT_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+(?:\s*;.*)?$"
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
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            image = stripped.split()[1]
            if not DIGEST_RE.search(image):
                findings.append(f"dockerfile_base_not_pinned:{image}")
    if re.search(r"\bapk\s+upgrade\b", content):
        findings.append("dockerfile_mutable_apk_upgrade")
    if re.search(r"python\s+-m\s+pip\s+install\s+--upgrade\s+[^\\\n]*[><~=]", content):
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
            # Candidate application images are validated separately as mandatory
            # immutable digest variables by the controlled-deployment preflight.
            continue
        if not DIGEST_RE.search(image):
            findings.append(f"compose_image_not_pinned:{path.name}:{number}:{image}")
    return findings


def collect_supply_chain_state(*, release: bool = False) -> dict[str, Any]:
    tracked = [
        ROOT / "Dockerfile",
        ROOT / "backend" / "requirements.txt",
        ROOT / "webapp" / "package.json",
        ROOT / "webapp" / "package-lock.json",
        ROOT / "deploy" / "docker-compose.server.yml",
        ROOT / "deploy" / "docker-compose.controlled.yml",
    ]
    findings: list[str] = []
    for path in tracked:
        if not path.is_file():
            findings.append(f"supply_chain_input_missing:{path.relative_to(ROOT)}")

    dockerfile = ROOT / "Dockerfile"
    requirements = ROOT / "backend" / "requirements.txt"
    if dockerfile.is_file():
        findings.extend(_dockerfile_findings(dockerfile))
    if requirements.is_file():
        findings.extend(_requirements_findings(requirements))
    for relative in (
        "deploy/docker-compose.server.yml",
        "deploy/docker-compose.controlled.yml",
    ):
        path = ROOT / relative
        if path.is_file():
            findings.extend(_compose_findings(path))

    evidence: dict[str, Any] = {
        "inputs": {
            str(path.relative_to(ROOT)): _sha256(path)
            for path in tracked
            if path.is_file()
        },
        "release_evidence_required": release,
    }
    if release:
        required = {
            "sbom": ROOT / "artifacts" / "supply-chain" / "sbom.spdx.json",
            "provenance": ROOT / "artifacts" / "supply-chain" / "provenance.json",
            "signature_bundle": ROOT / "artifacts" / "supply-chain" / "cosign.bundle.json",
        }
        for name, path in required.items():
            if not path.is_file() or path.stat().st_size == 0:
                findings.append(f"release_evidence_missing:{name}")
            else:
                evidence[name] = {
                    "path": str(path.relative_to(ROOT)),
                    "sha256": _sha256(path),
                }

    return {
        "schema": "nexus.supply-chain-qualification.v1",
        "status": "pass" if not findings else "fail",
        "findings": findings,
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = collect_supply_chain_state(release=args.release)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
