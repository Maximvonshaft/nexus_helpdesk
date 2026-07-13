from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "check_private_ai_runtime_deployment_manifest.py"
SCHEMA = ROOT / "infra" / "private-ai-runtime" / "deployment-manifest.v1.schema.json"


def load_module():
    spec = importlib.util.spec_from_file_location("private_ai_runtime_manifest", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_manifest() -> dict:
    return {
        "schema": "nexus.private_ai_runtime.deployment_manifest.v1",
        "manifest_version": "2026-07-13.1",
        "release_id": "private-ai-runtime-2026-07-13.1",
        "source": {"repository": "Maximvonshaft/nexus_helpdesk", "commit_sha": "a" * 40, "tree_sha": "c" * 40},
        "capability_contract": {
            "schema": "nexus.ai_runtime.capabilities.v1",
            "path": "config/private-ai-runtime-capabilities.v1.json",
            "sha256": "b" * 64,
        },
        "host_requirements": {
            "os_family": "linux", "architecture": "x86_64", "gpu_vendor": "nvidia",
            "min_gpu_vram_mib": 22528, "min_disk_free_mib": 102400, "driver_constraint": ">=550,<600",
        },
        "immutable_release": {
            "root": "/opt/nexus/private-ai-runtime/releases/private-ai-runtime-2026-07-13.1",
            "artifacts": [
                {"path": "services/gateway.py", "sha256": "b" * 64, "mode": "0644"},
                {"path": "systemd/nexus-private-ai-runtime.service", "sha256": "d" * 64, "mode": "0644"},
            ],
        },
        "mutable_state": [
            {"id": "qdrant_data", "path": "/var/lib/nexus/private-ai-runtime/qdrant", "purpose": "derived_vector_index", "authoritative": False, "backup_required": True},
            {"id": "model_cache", "path": "/var/cache/nexus/private-ai-runtime/models", "purpose": "model_cache", "authoritative": False, "backup_required": False},
        ],
        "secret_references": [
            {"id": "runtime_token", "kind": "file", "reference": "/etc/nexus/private-ai-runtime/secrets/runtime-token", "required": True}
        ],
        "images": [{"id": "qdrant", "reference": "docker.io/qdrant/qdrant@sha256:" + "e" * 64}],
        "models": [{"capability": "generation", "model_id": "approved-generation-model", "revision": "rev-2026-07-13", "sha256": "f" * 64}],
        "services": [{"name": "nexus-private-ai-runtime", "manager": "systemd", "definition_path": "systemd/nexus-private-ai-runtime.service", "sha256": "d" * 64}],
        "acceptance": {
            "commands": [["python3", "scripts/acceptance/check_runtime.py", "--manifest", "manifest.json"]],
            "required_checks": ["gpu_placement", "generation_hot_path", "retrieval", "voice", "metrics", "model_identity"],
            "network_mode": "read_only",
        },
        "rollback": {
            "target_release_id": "private-ai-runtime-2026-07-12.1",
            "package_path": "/opt/nexus/private-ai-runtime/rollback/private-ai-runtime-2026-07-12.1.tar.zst",
            "sha256": "1" * 64,
            "commands": [["python3", "scripts/rollback/activate.py", "--release", "private-ai-runtime-2026-07-12.1"]],
            "destructive": False,
        },
        "drift": {
            "tracked_paths": ["/opt/nexus/private-ai-runtime/current", "/etc/systemd/system/nexus-private-ai-runtime.service"],
            "result_path": "/var/lib/nexus/private-ai-runtime/reports/drift.json",
            "interval_seconds": 300,
            "fail_closed": True,
        },
    }


def test_valid_manifest_is_accepted():
    module = load_module()
    assert module.validate_manifest(valid_manifest())["source"]["commit_sha"] == "a" * 40


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda m: m.update(unexpected=True), "manifest_keys_invalid"),
        (lambda m: m["source"].update(commit_sha="main"), "source.commit_sha_invalid"),
        (lambda m: m["images"][0].update(reference="qdrant:latest"), "image_reference_requires_digest"),
        (lambda m: m["mutable_state"][0].update(path=m["immutable_release"]["root"] + "/qdrant"), "mutable_path_overlaps_immutable_root"),
        (lambda m: m["mutable_state"][0].update(authoritative=True), "mutable_state_must_not_be_authoritative"),
        (lambda m: m["acceptance"].update(commands=["python check.py"]), r"acceptance.commands\[0\]_must_be_argv"),
        (lambda m: m["rollback"].update(destructive=True), "rollback.destructive_must_be_false"),
        (lambda m: m["capability_contract"].update(schema="nexus.private_ai_runtime.capabilities.v2"), "capability_contract.schema_unsupported"),
    ],
)
def test_manifest_invariants_fail_closed(mutate, reason):
    module = load_module()
    manifest = valid_manifest()
    mutate(manifest)
    with pytest.raises(module.ManifestValidationError, match=reason):
        module.validate_manifest(manifest)


@pytest.mark.parametrize("field", ["value", "token", "password", "api_key", "private_key"])
def test_inline_secret_fields_are_rejected_recursively(field: str):
    module = load_module()
    manifest = valid_manifest()
    manifest["secret_references"][0][field] = "do-not-emit"
    with pytest.raises(module.ManifestValidationError, match="inline_secret_field_forbidden"):
        module.validate_manifest(manifest)


def test_duplicate_ids_and_artifact_paths_fail_closed():
    module = load_module()
    manifest = valid_manifest()
    manifest["mutable_state"].append(deepcopy(manifest["mutable_state"][0]))
    with pytest.raises(module.ManifestValidationError, match="mutable_state.id_duplicate"):
        module.validate_manifest(manifest)
    manifest = valid_manifest()
    manifest["immutable_release"]["artifacts"].append(deepcopy(manifest["immutable_release"]["artifacts"][0]))
    with pytest.raises(module.ManifestValidationError, match="immutable_release.artifact_path_duplicate"):
        module.validate_manifest(manifest)


def test_result_is_bounded_and_redacted():
    module = load_module()
    manifest = valid_manifest()
    manifest["secret_references"][0]["value"] = "super-secret-value"
    result = module.build_validation_result(manifest, finding_limit=3)
    encoded = json.dumps(result, sort_keys=True)
    assert result["schema"] == "nexus.private_ai_runtime.deployment_manifest_validation.v1"
    assert result["ok"] is False and len(result["findings"]) <= 3
    assert "super-secret-value" not in encoded and "runtime-token" not in encoded


def test_json_schema_required_keys_match_validator():
    module = load_module()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$id"] == "nexus.private_ai_runtime.deployment_manifest.v1"
    assert set(schema["required"]) == module.REQUIRED_TOP_LEVEL
    assert schema["additionalProperties"] is False


def test_cli_exit_status_and_bounded_output(tmp_path: Path):
    manifest_path, result_path = tmp_path / "manifest.json", tmp_path / "result.json"
    manifest_path.write_text(json.dumps(valid_manifest()), encoding="utf-8")
    command = [sys.executable, str(SCRIPT), "--manifest", str(manifest_path), "--output", str(result_path)]
    assert subprocess.run(command, check=False, capture_output=True, text=True).returncode == 0
    assert json.loads(result_path.read_text(encoding="utf-8"))["ok"] is True
    invalid = valid_manifest(); invalid["images"][0]["reference"] = "qdrant:latest"
    manifest_path.write_text(json.dumps(invalid), encoding="utf-8")
    assert subprocess.run(command, check=False, capture_output=True, text=True).returncode == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["findings"][0]["code"] == "image_reference_requires_digest"


def test_service_definition_hash_must_match_immutable_artifact():
    module = load_module()
    manifest = valid_manifest()
    manifest["services"][0]["sha256"] = "9" * 64
    with pytest.raises(module.ManifestValidationError, match="service.definition_sha256_mismatch"):
        module.validate_manifest(manifest)


def test_commands_cannot_embed_secret_arguments():
    module = load_module()
    manifest = valid_manifest()
    manifest["acceptance"]["commands"][0].append("--token=inline-secret")
    with pytest.raises(module.ManifestValidationError, match=r"acceptance.commands\[0\]_inline_secret_argument_forbidden"):
        module.validate_manifest(manifest)


def test_cli_rejects_oversized_manifest_before_json_parse(tmp_path: Path):
    module = load_module()
    manifest_path, result_path = tmp_path / "manifest.json", tmp_path / "result.json"
    manifest_path.write_bytes(b"{" + b" " * module.MAX_MANIFEST_BYTES + b"}")
    command = [sys.executable, str(SCRIPT), "--manifest", str(manifest_path), "--output", str(result_path)]
    assert subprocess.run(command, check=False, capture_output=True, text=True).returncode == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["findings"] == [{"code": "manifest_too_large", "path": "$"}]


def test_mutable_parent_path_cannot_enclose_immutable_release_root():
    module = load_module()
    manifest = valid_manifest()
    manifest["mutable_state"][0]["path"] = "/opt/nexus/private-ai-runtime"
    with pytest.raises(module.ManifestValidationError, match="mutable_path_overlaps_immutable_root"):
        module.validate_manifest(manifest)


def test_repository_identity_rejects_whitespace():
    module = load_module()
    manifest = valid_manifest()
    manifest["source"]["repository"] = "Maximvonshaft /nexus_helpdesk"
    with pytest.raises(module.ManifestValidationError, match="source.repository_invalid"):
        module.validate_manifest(manifest)


def test_commands_reject_secret_environment_assignments():
    module = load_module()
    manifest = valid_manifest()
    manifest["acceptance"]["commands"][0].append("TOKEN=inline-secret")
    with pytest.raises(module.ManifestValidationError, match=r"acceptance.commands\[0\]_inline_secret_argument_forbidden"):
        module.validate_manifest(manifest)


@pytest.mark.parametrize(
    ("section", "command"),
    [
        ("acceptance", ["bash", "-c", "python3 scripts/acceptance/check_runtime.py"]),
        ("acceptance", ["/bin/sh", "-c", "python3 scripts/acceptance/check_runtime.py"]),
        ("acceptance", ["env", "bash", "-c", "python3 scripts/acceptance/check_runtime.py"]),
        ("rollback", ["powershell.exe", "-Command", "python scripts/rollback/activate.py"]),
    ],
)
def test_commands_reject_shell_execution(section: str, command: list[str]):
    module = load_module()
    manifest = valid_manifest()
    manifest[section]["commands"] = [command]
    with pytest.raises(
        module.ManifestValidationError,
        match=rf"{section}\.commands\[0\]_shell_execution_forbidden",
    ):
        module.validate_manifest(manifest)
