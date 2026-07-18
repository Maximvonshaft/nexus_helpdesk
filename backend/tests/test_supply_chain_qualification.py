from __future__ import annotations

import importlib.util
import json
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
    assert result["evidence"]["candidate_tree_mutated"] is False
    assert "Dockerfile" in result["evidence"]["inputs"]
    assert "backend/requirements.txt" in result["evidence"]["inputs"]


def test_release_mode_requires_external_evidence_directory(monkeypatch):
    monkeypatch.delenv(module.EVIDENCE_DIR_ENV, raising=False)
    result = module.collect_supply_chain_state(release=True)
    assert result["status"] == "fail"
    assert result["findings"] == ["release_evidence_dir_missing"]


def test_release_mode_rejects_evidence_inside_candidate_tree(tmp_path):
    inside = ROOT / "artifacts" / "supply-chain-test"
    result = module.collect_supply_chain_state(release=True, evidence_dir=inside)
    assert result["status"] == "fail"
    assert "release_evidence_inside_candidate_tree" in result["findings"]


def test_release_mode_accepts_structured_external_evidence(tmp_path):
    (tmp_path / "sbom.spdx.json").write_text(
        json.dumps({"spdxVersion": "SPDX-2.3"}),
        encoding="utf-8",
    )
    (tmp_path / "provenance.json").write_text(
        json.dumps(
            {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [
                    {
                        "name": "ghcr.io/maximvonshaft/nexus_helpdesk",
                        "digest": {"sha256": "a" * 64},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "cosign.bundle.json").write_text(
        json.dumps({"verificationMaterial": {}}),
        encoding="utf-8",
    )

    result = module.collect_supply_chain_state(
        release=True,
        evidence_dir=tmp_path,
    )

    assert result["status"] == "pass", result["findings"]
    assert result["findings"] == []
    assert result["evidence"]["evidence_dir"] == str(tmp_path.resolve())
    assert result["evidence"]["candidate_tree_mutated"] is False


def test_dockerfile_comments_do_not_create_mutable_instruction_findings(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM example.invalid/runtime@sha256:" + "a" * 64 + "\n"
        "# Security updates must not use apk upgrade during the build.\n"
        "RUN apk add --no-cache ca-certificates\n",
        encoding="utf-8",
    )
    assert module._dockerfile_findings(dockerfile) == []
