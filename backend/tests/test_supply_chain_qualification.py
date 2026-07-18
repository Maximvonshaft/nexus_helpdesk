from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = _load_module(
    "nexus_supply_chain_qualification",
    "scripts/qualification/supply_chain.py",
)
assembler = _load_module(
    "nexus_supply_chain_assembly",
    "scripts/release/assemble_supply_chain_evidence.py",
)


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


def test_release_mode_rejects_evidence_inside_candidate_tree():
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


def test_assembler_requires_external_output_directory(monkeypatch):
    monkeypatch.delenv(assembler.EVIDENCE_DIR_ENV, raising=False)
    with pytest.raises(ValueError, match="supply_chain_evidence_dir_required"):
        assembler._resolve_output_dir(None)
    with pytest.raises(
        ValueError,
        match="supply_chain_evidence_inside_candidate_tree",
    ):
        assembler._resolve_output_dir(ROOT / "artifacts" / "supply-chain")


def test_assembler_binds_external_evidence_to_clean_candidate(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    output = tmp_path / "assembled"
    sources.mkdir()
    sbom_source = sources / "sbom.json"
    signature_source = sources / "signature.json"
    sbom_source.write_text(
        json.dumps({"spdxVersion": "SPDX-2.3"}),
        encoding="utf-8",
    )
    signature_source.write_text(
        json.dumps({"verificationMaterial": {"certificate": "bounded"}}),
        encoding="utf-8",
    )

    def fake_git(*args: str) -> str:
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "b" * 40
        if args == ("rev-parse", "HEAD^{tree}"):
            return "c" * 40
        if args == ("show", "-s", "--format=%ct", "HEAD"):
            return "1700000000"
        raise AssertionError(args)

    monkeypatch.setattr(assembler, "_git", fake_git)
    result = assembler.assemble(
        image="ghcr.io/maximvonshaft/nexus_helpdesk@sha256:" + "a" * 64,
        sbom_source=sbom_source,
        signature_bundle_source=signature_source,
        output_dir=output,
    )

    assert result["source_sha"] == "b" * 40
    assert result["tree_sha"] == "c" * 40
    assert result["evidence_dir"] == str(output.resolve())
    assert result["candidate_tree_mutated"] is False
    assert (output / "sbom.spdx.json").is_file()
    assert (output / "provenance.json").is_file()
    assert (output / "cosign.bundle.json").is_file()


def test_dockerfile_comments_do_not_create_mutable_instruction_findings(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM example.invalid/runtime@sha256:" + "a" * 64 + "\n"
        "# Security updates must not use apk upgrade during the build.\n"
        "RUN apk add --no-cache ca-certificates\n",
        encoding="utf-8",
    )
    assert module._dockerfile_findings(dockerfile) == []
