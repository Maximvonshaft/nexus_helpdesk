#!/usr/bin/env python3
"""Assemble immutable supply-chain evidence for an already built image.

This command never invents an SBOM or signature. It requires externally generated
JSON evidence, binds it to the exact clean Git candidate and writes the canonical
artifact names consumed by ``scripts/qualification/supply_chain.py --release``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
IMAGE_DIGEST_RE = re.compile(r"^[a-z0-9._/-]+(?:\:[a-z0-9._-]+)?@sha256:[0-9a-f]{64}$")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, *, label: str) -> Any:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label}_missing")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError(f"{label}_too_large")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}_invalid_json") from exc


def _source_epoch() -> int:
    raw = os.getenv("SOURCE_DATE_EPOCH", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("source_date_epoch_invalid") from exc
        if value <= 0:
            raise ValueError("source_date_epoch_invalid")
        return value
    return int(_git("show", "-s", "--format=%ct", "HEAD"))


def build_provenance(*, image: str, sbom_path: Path, signature_bundle_path: Path) -> dict[str, Any]:
    if not IMAGE_DIGEST_RE.fullmatch(image):
        raise ValueError("immutable_image_digest_required")
    source_sha = _git("rev-parse", "HEAD")
    tree_sha = _git("rev-parse", "HEAD^{tree}")
    dirty = _git("status", "--porcelain")
    if dirty:
        raise ValueError("candidate_worktree_not_clean")
    inputs = [
        ROOT / "Dockerfile",
        ROOT / "backend" / "requirements.txt",
        ROOT / "webapp" / "package.json",
        ROOT / "webapp" / "package-lock.json",
        ROOT / "deploy" / "docker-compose.controlled.yml",
        ROOT / "deploy" / ".env.controlled.example",
    ]
    missing = [str(path.relative_to(ROOT)) for path in inputs if not path.is_file()]
    if missing:
        raise ValueError(f"provenance_input_missing:{','.join(missing)}")
    epoch = _source_epoch()
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": image.split("@", 1)[0], "digest": {"sha256": image.rsplit(":", 1)[1]}}],
        "predicate": {
            "buildDefinition": {
                "buildType": "https://nexus.invalid/build/container/v1",
                "externalParameters": {
                    "source_sha": source_sha,
                    "tree_sha": tree_sha,
                    "source_date_epoch": epoch,
                },
                "internalParameters": {
                    "builder_id": os.getenv("NEXUS_BUILDER_ID", "unverified-builder")[:200],
                },
                "resolvedDependencies": [
                    {
                        "uri": f"git+https://github.com/Maximvonshaft/nexus_helpdesk@{source_sha}",
                        "digest": {"sha1": source_sha, "gitTree": tree_sha},
                    },
                    *[
                        {
                            "uri": str(path.relative_to(ROOT)),
                            "digest": {"sha256": _sha256(path)},
                        }
                        for path in inputs
                    ],
                ],
            },
            "runDetails": {
                "builder": {"id": os.getenv("NEXUS_BUILDER_ID", "unverified-builder")[:200]},
                "metadata": {
                    "invocationId": os.getenv("NEXUS_BUILD_INVOCATION_ID", "unverified-invocation")[:200],
                    "startedOn": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
                    "finishedOn": datetime.now(timezone.utc).isoformat(),
                },
                "byproducts": [
                    {"name": "sbom.spdx.json", "digest": {"sha256": _sha256(sbom_path)}},
                    {"name": "cosign.bundle.json", "digest": {"sha256": _sha256(signature_bundle_path)}},
                ],
            },
        },
    }


def assemble(*, image: str, sbom_source: Path, signature_bundle_source: Path, output_dir: Path) -> dict[str, Any]:
    sbom = _load_json(sbom_source, label="sbom")
    signature_bundle = _load_json(signature_bundle_source, label="signature_bundle")
    if not isinstance(sbom, dict):
        raise ValueError("sbom_root_invalid")
    if not isinstance(signature_bundle, dict):
        raise ValueError("signature_bundle_root_invalid")
    output_dir.mkdir(parents=True, exist_ok=True)
    sbom_target = output_dir / "sbom.spdx.json"
    signature_target = output_dir / "cosign.bundle.json"
    shutil.copyfile(sbom_source, sbom_target)
    shutil.copyfile(signature_bundle_source, signature_target)
    provenance = build_provenance(
        image=image,
        sbom_path=sbom_target,
        signature_bundle_path=signature_target,
    )
    provenance_target = output_dir / "provenance.json"
    provenance_target.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema": "nexus.supply-chain-evidence-assembly.v1",
        "image": image,
        "source_sha": _git("rev-parse", "HEAD"),
        "tree_sha": _git("rev-parse", "HEAD^{tree}"),
        "artifacts": {
            "sbom": {"path": str(sbom_target), "sha256": _sha256(sbom_target)},
            "provenance": {"path": str(provenance_target), "sha256": _sha256(provenance_target)},
            "signature_bundle": {"path": str(signature_target), "sha256": _sha256(signature_target)},
        },
        "generated_evidence_fabricated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--sbom-source", type=Path, required=True)
    parser.add_argument("--signature-bundle-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "supply-chain")
    args = parser.parse_args()
    result = assemble(
        image=args.image,
        sbom_source=args.sbom_source,
        signature_bundle_source=args.signature_bundle_source,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
