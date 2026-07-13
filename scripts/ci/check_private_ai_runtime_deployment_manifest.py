#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

MANIFEST_SCHEMA = "nexus.private_ai_runtime.deployment_manifest.v1"
CAPABILITY_SCHEMA = "nexus.ai_runtime.capabilities.v1"
RESULT_SCHEMA = "nexus.private_ai_runtime.deployment_manifest_validation.v1"
REQUIRED_TOP_LEVEL = {
    "schema",
    "manifest_version",
    "release_id",
    "source",
    "capability_contract",
    "host_requirements",
    "immutable_release",
    "mutable_state",
    "secret_references",
    "images",
    "models",
    "services",
    "acceptance",
    "rollback",
    "drift",
}
FORBIDDEN_INLINE_SECRET_FIELDS = {
    "value",
    "token",
    "password",
    "secret",
    "api_key",
    "private_key",
    "credential",
    "authorization",
}
REQUIRED_ACCEPTANCE_CHECKS = {
    "gpu_placement",
    "generation_hot_path",
    "retrieval",
    "voice",
    "metrics",
    "model_identity",
}
FORBIDDEN_COMMAND_LAUNCHERS = frozenset({"env"})
FORBIDDEN_SHELL_EXECUTABLES = frozenset(
    {
        "sh",
        "bash",
        "dash",
        "ash",
        "zsh",
        "ksh",
        "fish",
        "csh",
        "tcsh",
        "pwsh",
        "pwsh.exe",
        "powershell",
        "powershell.exe",
        "cmd",
        "cmd.exe",
    }
)
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
VERSION_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+$")
RELEASE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{4,127}$")
MODE_RE = re.compile(r"^0[0-7]{3}$")
IMAGE_DIGEST_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SECRET_STORE_PREFIXES = ("vault://", "aws-sm://", "gcp-sm://", "azure-kv://")
MAX_MANIFEST_BYTES = 1_048_576
SECRET_ARGUMENT_RE = re.compile(
    r"(?i)^--?(?:(?:auth[-_])?token|password|secret|api[-_]key|private[-_]key)(?:=|$)"
)
SECRET_ENV_RE = re.compile(
    r"(?i)^(?:token|password|secret|api[_-]?key|private[_-]?key|authorization)="
)


class ManifestValidationError(ValueError):
    """Fail-closed deployment-manifest validation error with a bounded reason code."""

    def __init__(self, code: str, path: str = "$") -> None:
        super().__init__(code)
        self.code = code
        self.path = path


def _fail(code: str, path: str = "$") -> None:
    raise ManifestValidationError(code, path)


def _exact_keys(value: Mapping[str, Any], expected: set[str], *, field: str) -> None:
    if set(value) != expected:
        _fail(f"{field}_keys_invalid", field)


def _object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{field}_must_be_object", field)
    return value


def _string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field}_invalid", field)
    return value


def _bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{field}_invalid", field)
    return value


def _integer(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(f"{field}_invalid", field)
    return value


def _string_list(value: Any, *, field: str, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        _fail(f"{field}_must_be_string_list", field)
    items = [_string(item, field=f"{field}[{index}]") for index, item in enumerate(value)]
    if len(items) != len(set(items)):
        _fail(f"{field}_must_be_unique", field)
    return items


def _sha40(value: Any, *, field: str) -> str:
    value = _string(value, field=field)
    if not HEX40.fullmatch(value):
        _fail(f"{field}_invalid", field)
    return value


def _sha64(value: Any, *, field: str) -> str:
    value = _string(value, field=field)
    if not HEX64.fullmatch(value):
        _fail(f"{field}_invalid", field)
    return value


def _id(value: Any, *, field: str) -> str:
    value = _string(value, field=field)
    if not ID_RE.fullmatch(value):
        _fail(f"{field}_invalid", field)
    return value


def _absolute_path(value: Any, *, field: str) -> str:
    value = _string(value, field=field)
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or value != str(path):
        _fail(f"{field}_invalid", field)
    return value


def _relative_path(value: Any, *, field: str) -> str:
    value = _string(value, field=field)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {".", ""} or value != str(path):
        _fail(f"{field}_invalid", field)
    return value


def _under(candidate: str, root: str) -> bool:
    candidate_path = PurePosixPath(candidate)
    root_path = PurePosixPath(root)
    return candidate_path == root_path or root_path in candidate_path.parents


def _paths_overlap(left: str, right: str) -> bool:
    return _under(left, right) or _under(right, left)


def _scan_for_inline_secret_fields(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and key.lower() in FORBIDDEN_INLINE_SECRET_FIELDS:
                _fail("inline_secret_field_forbidden", child_path)
            _scan_for_inline_secret_fields(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_inline_secret_fields(child, path=f"{path}[{index}]")


def _command_token_basename(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).name.lower()


def _argv_commands(value: Any, *, field: str) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        _fail(f"{field}_must_be_non_empty_list", field)
    commands: list[list[str]] = []
    for index, command in enumerate(value):
        command_field = f"{field}[{index}]"
        if not isinstance(command, list) or not command:
            _fail(f"{command_field}_must_be_argv", command_field)
        argv = [_string(arg, field=f"{command_field}[{arg_index}]") for arg_index, arg in enumerate(command)]
        if any("\x00" in arg or "\n" in arg or "\r" in arg for arg in argv):
            _fail(f"{command_field}_contains_control_character", command_field)
        if any(
            SECRET_ARGUMENT_RE.search(arg)
            or SECRET_ENV_RE.search(arg)
            or arg.lower().startswith("authorization:")
            for arg in argv
        ):
            _fail(f"{command_field}_inline_secret_argument_forbidden", command_field)
        if (
            _command_token_basename(argv[0]) in FORBIDDEN_COMMAND_LAUNCHERS
            or any(_command_token_basename(arg) in FORBIDDEN_SHELL_EXECUTABLES for arg in argv)
        ):
            _fail(f"{command_field}_shell_execution_forbidden", command_field)
        commands.append(argv)
    return commands


def validate_manifest(raw: Any) -> dict[str, Any]:
    manifest = _object(raw, field="manifest")
    _scan_for_inline_secret_fields(manifest)
    _exact_keys(manifest, REQUIRED_TOP_LEVEL, field="manifest")
    if manifest["schema"] != MANIFEST_SCHEMA:
        _fail("manifest_schema_unsupported", "schema")
    version = _string(manifest["manifest_version"], field="manifest_version")
    if not VERSION_RE.fullmatch(version):
        _fail("manifest_version_invalid", "manifest_version")
    release_id = _string(manifest["release_id"], field="release_id")
    if not RELEASE_RE.fullmatch(release_id):
        _fail("release_id_invalid", "release_id")

    source = _object(manifest["source"], field="source")
    _exact_keys(source, {"repository", "commit_sha", "tree_sha"}, field="source")
    repository = _string(source["repository"], field="source.repository")
    if not REPOSITORY_RE.fullmatch(repository):
        _fail("source.repository_invalid", "source.repository")
    _sha40(source["commit_sha"], field="source.commit_sha")
    _sha40(source["tree_sha"], field="source.tree_sha")

    capability = _object(manifest["capability_contract"], field="capability_contract")
    _exact_keys(capability, {"schema", "path", "sha256"}, field="capability_contract")
    if capability["schema"] != CAPABILITY_SCHEMA:
        _fail("capability_contract.schema_unsupported", "capability_contract.schema")
    _relative_path(capability["path"], field="capability_contract.path")
    _sha64(capability["sha256"], field="capability_contract.sha256")

    host = _object(manifest["host_requirements"], field="host_requirements")
    _exact_keys(
        host,
        {"os_family", "architecture", "gpu_vendor", "min_gpu_vram_mib", "min_disk_free_mib", "driver_constraint"},
        field="host_requirements",
    )
    if host["os_family"] != "linux":
        _fail("host_requirements.os_family_unsupported", "host_requirements.os_family")
    if host["architecture"] not in {"x86_64", "aarch64"}:
        _fail("host_requirements.architecture_unsupported", "host_requirements.architecture")
    if host["gpu_vendor"] != "nvidia":
        _fail("host_requirements.gpu_vendor_unsupported", "host_requirements.gpu_vendor")
    _integer(host["min_gpu_vram_mib"], field="host_requirements.min_gpu_vram_mib", minimum=1024, maximum=1_048_576)
    _integer(host["min_disk_free_mib"], field="host_requirements.min_disk_free_mib", minimum=1024, maximum=100_000_000)
    _string(host["driver_constraint"], field="host_requirements.driver_constraint")

    immutable = _object(manifest["immutable_release"], field="immutable_release")
    _exact_keys(immutable, {"root", "artifacts"}, field="immutable_release")
    immutable_root = _absolute_path(immutable["root"], field="immutable_release.root")
    artifacts = immutable["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        _fail("immutable_release.artifacts_must_be_non_empty_list", "immutable_release.artifacts")
    artifact_paths: set[str] = set()
    artifact_hashes: dict[str, str] = {}
    for index, artifact_raw in enumerate(artifacts):
        field = f"immutable_release.artifacts[{index}]"
        artifact = _object(artifact_raw, field=field)
        _exact_keys(artifact, {"path", "sha256", "mode"}, field=field)
        path = _relative_path(artifact["path"], field=f"{field}.path")
        if path in artifact_paths:
            _fail("immutable_release.artifact_path_duplicate", f"{field}.path")
        artifact_paths.add(path)
        artifact_hashes[path] = _sha64(artifact["sha256"], field=f"{field}.sha256")
        mode = _string(artifact["mode"], field=f"{field}.mode")
        if not MODE_RE.fullmatch(mode):
            _fail(f"{field}.mode_invalid", f"{field}.mode")

    mutable_state = manifest["mutable_state"]
    if not isinstance(mutable_state, list) or not mutable_state:
        _fail("mutable_state_must_be_non_empty_list", "mutable_state")
    mutable_ids: set[str] = set()
    mutable_paths: set[str] = set()
    for index, state_raw in enumerate(mutable_state):
        field = f"mutable_state[{index}]"
        state = _object(state_raw, field=field)
        _exact_keys(state, {"id", "path", "purpose", "authoritative", "backup_required"}, field=field)
        state_id = _id(state["id"], field=f"{field}.id")
        if state_id in mutable_ids:
            _fail("mutable_state.id_duplicate", f"{field}.id")
        mutable_ids.add(state_id)
        path = _absolute_path(state["path"], field=f"{field}.path")
        if path in mutable_paths:
            _fail("mutable_state.path_duplicate", f"{field}.path")
        mutable_paths.add(path)
        if _paths_overlap(path, immutable_root):
            _fail("mutable_path_overlaps_immutable_root", f"{field}.path")
        _string(state["purpose"], field=f"{field}.purpose")
        if state["authoritative"] is not False:
            _fail("mutable_state_must_not_be_authoritative", f"{field}.authoritative")
        _bool(state["backup_required"], field=f"{field}.backup_required")

    secret_refs = manifest["secret_references"]
    if not isinstance(secret_refs, list) or not secret_refs:
        _fail("secret_references_must_be_non_empty_list", "secret_references")
    secret_ids: set[str] = set()
    for index, ref_raw in enumerate(secret_refs):
        field = f"secret_references[{index}]"
        ref = _object(ref_raw, field=field)
        _exact_keys(ref, {"id", "kind", "reference", "required"}, field=field)
        ref_id = _id(ref["id"], field=f"{field}.id")
        if ref_id in secret_ids:
            _fail("secret_reference.id_duplicate", f"{field}.id")
        secret_ids.add(ref_id)
        kind = ref["kind"]
        if kind not in {"file", "secret_store"}:
            _fail("secret_reference.kind_unsupported", f"{field}.kind")
        reference = _string(ref["reference"], field=f"{field}.reference")
        if kind == "file":
            _absolute_path(reference, field=f"{field}.reference")
            if _paths_overlap(reference, immutable_root):
                _fail("secret_reference_inside_immutable_release", f"{field}.reference")
        elif not reference.startswith(SECRET_STORE_PREFIXES):
            _fail("secret_store_reference_scheme_unsupported", f"{field}.reference")
        _bool(ref["required"], field=f"{field}.required")

    images = manifest["images"]
    if not isinstance(images, list) or not images:
        _fail("images_must_be_non_empty_list", "images")
    image_ids: set[str] = set()
    for index, image_raw in enumerate(images):
        field = f"images[{index}]"
        image = _object(image_raw, field=field)
        _exact_keys(image, {"id", "reference"}, field=field)
        image_id = _id(image["id"], field=f"{field}.id")
        if image_id in image_ids:
            _fail("image.id_duplicate", f"{field}.id")
        image_ids.add(image_id)
        reference = _string(image["reference"], field=f"{field}.reference")
        if not IMAGE_DIGEST_RE.fullmatch(reference):
            _fail("image_reference_requires_digest", f"{field}.reference")

    models = manifest["models"]
    if not isinstance(models, list) or not models:
        _fail("models_must_be_non_empty_list", "models")
    model_capabilities: set[str] = set()
    for index, model_raw in enumerate(models):
        field = f"models[{index}]"
        model = _object(model_raw, field=field)
        _exact_keys(model, {"capability", "model_id", "revision", "sha256"}, field=field)
        model_capability = _id(model["capability"], field=f"{field}.capability")
        if model_capability in model_capabilities:
            _fail("model.capability_duplicate", f"{field}.capability")
        model_capabilities.add(model_capability)
        _string(model["model_id"], field=f"{field}.model_id")
        _string(model["revision"], field=f"{field}.revision")
        _sha64(model["sha256"], field=f"{field}.sha256")

    services = manifest["services"]
    if not isinstance(services, list) or not services:
        _fail("services_must_be_non_empty_list", "services")
    service_names: set[str] = set()
    for index, service_raw in enumerate(services):
        field = f"services[{index}]"
        service = _object(service_raw, field=field)
        _exact_keys(service, {"name", "manager", "definition_path", "sha256"}, field=field)
        name = _id(service["name"], field=f"{field}.name")
        if name in service_names:
            _fail("service.name_duplicate", f"{field}.name")
        service_names.add(name)
        if service["manager"] not in {"systemd", "compose"}:
            _fail("service.manager_unsupported", f"{field}.manager")
        definition_path = _relative_path(service["definition_path"], field=f"{field}.definition_path")
        if definition_path not in artifact_paths:
            _fail("service.definition_not_in_immutable_artifacts", f"{field}.definition_path")
        service_sha = _sha64(service["sha256"], field=f"{field}.sha256")
        if artifact_hashes[definition_path] != service_sha:
            _fail("service.definition_sha256_mismatch", f"{field}.sha256")

    acceptance = _object(manifest["acceptance"], field="acceptance")
    _exact_keys(acceptance, {"commands", "required_checks", "network_mode"}, field="acceptance")
    _argv_commands(acceptance["commands"], field="acceptance.commands")
    checks = set(_string_list(acceptance["required_checks"], field="acceptance.required_checks"))
    if not REQUIRED_ACCEPTANCE_CHECKS.issubset(checks):
        _fail("acceptance.required_checks_incomplete", "acceptance.required_checks")
    if acceptance["network_mode"] not in {"offline", "read_only"}:
        _fail("acceptance.network_mode_unsupported", "acceptance.network_mode")

    rollback = _object(manifest["rollback"], field="rollback")
    _exact_keys(rollback, {"target_release_id", "package_path", "sha256", "commands", "destructive"}, field="rollback")
    target_release_id = _string(rollback["target_release_id"], field="rollback.target_release_id")
    if not RELEASE_RE.fullmatch(target_release_id) or target_release_id == release_id:
        _fail("rollback.target_release_id_invalid", "rollback.target_release_id")
    package_path = _absolute_path(rollback["package_path"], field="rollback.package_path")
    if _paths_overlap(package_path, immutable_root):
        _fail("rollback.package_inside_immutable_release", "rollback.package_path")
    _sha64(rollback["sha256"], field="rollback.sha256")
    _argv_commands(rollback["commands"], field="rollback.commands")
    if rollback["destructive"] is not False:
        _fail("rollback.destructive_must_be_false", "rollback.destructive")

    drift = _object(manifest["drift"], field="drift")
    _exact_keys(drift, {"tracked_paths", "result_path", "interval_seconds", "fail_closed"}, field="drift")
    tracked_paths = _string_list(drift["tracked_paths"], field="drift.tracked_paths")
    for index, path in enumerate(tracked_paths):
        _absolute_path(path, field=f"drift.tracked_paths[{index}]")
    result_path = _absolute_path(drift["result_path"], field="drift.result_path")
    if _paths_overlap(result_path, immutable_root):
        _fail("drift.result_inside_immutable_release", "drift.result_path")
    _integer(drift["interval_seconds"], field="drift.interval_seconds", minimum=60, maximum=86_400)
    if drift["fail_closed"] is not True:
        _fail("drift.fail_closed_must_be_true", "drift.fail_closed")

    return manifest


def _manifest_sha256(raw: Any) -> str | None:
    try:
        payload = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256(payload).hexdigest()


def build_validation_result(raw: Any, *, finding_limit: int = 20) -> dict[str, Any]:
    if isinstance(finding_limit, bool) or not isinstance(finding_limit, int) or not 1 <= finding_limit <= 100:
        raise ValueError("finding_limit_invalid")
    findings: list[dict[str, str]] = []
    source_commit_sha: str | None = None
    try:
        validated = validate_manifest(raw)
        source_commit_sha = validated["source"]["commit_sha"]
    except ManifestValidationError as exc:
        findings.append({"code": exc.code, "path": exc.path})
    except (RecursionError, TypeError, ValueError):
        findings.append({"code": "manifest_validation_internal_error", "path": "$"})
    bounded = findings[:finding_limit]
    return {
        "schema": RESULT_SCHEMA,
        "ok": not findings,
        "manifest_sha256": _manifest_sha256(raw),
        "source_commit_sha": source_commit_sha,
        "finding_count": len(findings),
        "findings": bounded,
    }


def _read_manifest(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ManifestValidationError("manifest_too_large", "$")
        return json.loads(payload.decode("utf-8"))
    except ManifestValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ManifestValidationError("manifest_read_or_json_error", "$") from exc


def _write_result(path: Path, result: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a bounded Private AI Runtime deployment manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--finding-limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        raw = _read_manifest(args.manifest)
        result = build_validation_result(raw, finding_limit=args.finding_limit)
    except ManifestValidationError as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "manifest_sha256": None,
            "source_commit_sha": None,
            "finding_count": 1,
            "findings": [{"code": exc.code, "path": exc.path}],
        }
    except (OSError, ValueError):
        result = {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "manifest_sha256": None,
            "source_commit_sha": None,
            "finding_count": 1,
            "findings": [{"code": "validation_result_write_or_argument_error", "path": "$"}],
        }
    try:
        _write_result(args.output, result)
    except OSError:
        return 2
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
