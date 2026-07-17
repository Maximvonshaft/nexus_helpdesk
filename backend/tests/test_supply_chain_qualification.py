from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "nexus_supply_chain_qualification",
    ROOT / "scripts" / "qualification" / "supply_chain.py",
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_current_supply_chain_inputs_are_immutable():
    result = module.collect_supply_chain_state()
    assert result["status"] == "pass", result["findings"]
    assert result["findings"] == []
    assert "Dockerfile" in result["evidence"]["inputs"]
    assert "backend/requirements.txt" in result["evidence"]["inputs"]


def test_release_mode_requires_sbom_provenance_and_signature():
    result = module.collect_supply_chain_state(release=True)
    assert result["status"] == "fail"
    assert {
        "release_evidence_missing:sbom",
        "release_evidence_missing:provenance",
        "release_evidence_missing:signature_bundle",
    }.issubset(set(result["findings"]))

def test_dockerfile_comments_do_not_create_mutable_instruction_findings(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM example.invalid/runtime@sha256:" + "a" * 64 + "\n"
        "# Security updates must not use apk upgrade during the build.\n"
        "RUN apk add --no-cache ca-certificates\n",
        encoding="utf-8",
    )
    assert module._dockerfile_findings(dockerfile) == []
