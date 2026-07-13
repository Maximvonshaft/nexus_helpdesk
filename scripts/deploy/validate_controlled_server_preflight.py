#!/usr/bin/env python3
"""Fail-closed preflight for the Swiss controlled-server deployment."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SECRET_FILE_BYTES = 64 * 1024
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_IMAGE = re.compile(
    r"^ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$"
)
_DIGEST_REFERENCE = re.compile(r"^[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_MIGRATION = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_APP_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
_COMPOSE_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_ATTESTATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")

SAFE_CONTROLS = {
    "TENANT_RUNTIME_AUTHORITY_MODE": "enforce",
    "AUTO_INIT_DB": "false",
    "SEED_DEMO_DATA": "false",
    "ALLOW_DEV_AUTH": "false",
    "ALLOW_LEGACY_INTEGRATION_API_KEY": "false",
    "STORAGE_BACKEND": "local",
    "UPLOAD_ROOT": "/app/backend/uploads",
    "LOCAL_STORAGE_BACKUP_REQUIRED": "true",
    "LOCAL_STORAGE_BACKUP_PATH": "/var/backups/nexusdesk/uploads",
    "LOCAL_STORAGE_BACKUP_ACKNOWLEDGED": "true",
    "REQUIRE_REMOTE_STORAGE_IN_PRODUCTION": "false",
    "WEBCHAT_ALLOW_NO_ORIGIN": "false",
    "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT": "false",
    "WEBCHAT_AI_ENABLED": "false",
    "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
    "WEBCHAT_AI_RECONCILER_ENABLED": "false",
    "WEBCHAT_VOICE_ENABLED": "false",
    "PROVIDER_RUNTIME_ENABLED": "false",
    "PROVIDER_RUNTIME_KILL_SWITCH": "true",
    "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
    "PRIVATE_AI_RUNTIME_ENABLED": "false",
    "ENABLE_OUTBOUND_DISPATCH": "false",
    "OUTBOUND_PROVIDER": "disabled",
    "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED": "false",
    "WHATSAPP_NATIVE_ENABLED": "false",
    "WHATSAPP_DISPATCH_MODE": "disabled",
    "EMAIL_MAILBOX_SYNC_ENABLED": "false",
    "ALLOW_LEGACY_ORIGINLESS_OUTBOUND": "false",
    "WEBCHAT_TRACKING_FACT_LOOKUP_ENABLED": "false",
    "SPEEDAF_MCP_ENABLED": "false",
    "SPEEDAF_TRACK_QUERY_ENABLED": "false",
    "SPEEDAF_WORK_ORDER_CREATE_ENABLED": "false",
    "SPEEDAF_UPDATE_ADDRESS_ENABLED": "false",
    "SPEEDAF_CANCEL_ENABLED": "false",
    "SPEEDAF_VOICE_CALLBACK_ENABLED": "false",
    "OPERATIONS_DISPATCH_MODE": "disabled",
    "OPERATIONS_DISPATCH_ADAPTER": "disabled",
    "WEBCALL_AI_PRODUCTION_ENABLED": "false",
    "WEBCALL_AI_AGENT_ENABLED": "false",
    "WEBCALL_AI_KILL_SWITCH": "true",
    "KNOWLEDGE_EMBEDDINGS_ENABLED": "false",
}

# Retired transport controls are assembled so this preflight does not become a
# new classified compatibility reference. Omitted values rely on fail-closed
# application defaults; present values must still remain disabled.
_RETIRED_PREFIX = "EXTERNAL_" + "CHANNEL_"
OPTIONAL_DISABLED_CONTROLS = {
    _RETIRED_PREFIX + "DEPLOYMENT_MODE": "disabled",
    _RETIRED_PREFIX + "TRANSPORT": "disabled",
    _RETIRED_PREFIX + "SYNC_ENABLED": "false",
    _RETIRED_PREFIX + "INBOUND_AUTO_SYNC_ENABLED": "false",
    _RETIRED_PREFIX + "EVENT_DRIVER_ENABLED": "false",
    _RETIRED_PREFIX + "BRIDGE_ENABLED": "false",
    _RETIRED_PREFIX + "CLI_FALLBACK_ENABLED": "false",
}


class PreflightError(ValueError):
    pass


def _load_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise PreflightError(f"manifest_invalid:{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError("manifest_json_invalid") from exc
    if not isinstance(payload, dict):
        raise PreflightError("manifest_object_required")
    return payload


def _parse_env(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise PreflightError("env_file_invalid")
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PreflightError("env_file_unreadable") from exc
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in raw:
            raise PreflightError(f"env_line_invalid:{number}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key):
            raise PreflightError(f"env_key_invalid:{number}")
        if key in values:
            raise PreflightError(f"env_key_duplicate:{key}")
        value = value.strip()
        if any(char in value for char in "\r\n\x00"):
            raise PreflightError(f"env_value_invalid:{key}")
        values[key] = value
    return values


def _validate_compose(path: Path) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise PreflightError("compose_file_invalid")
    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^\s*build\s*:", text):
        raise PreflightError("compose_build_forbidden")
    if "${CONTROLLED_IMAGE:?" not in text:
        raise PreflightError("compose_digest_variable_missing")
    for forbidden in (
        "external: true",
        "production_runtime",
        "whatsapp-sidecar",
        "node:22-bookworm-slim",
        ":latest",
    ):
        if forbidden in text:
            raise PreflightError(f"compose_forbidden:{forbidden}")
    required_services = (
        "migrate-controlled:",
        "app-controlled:",
        "worker-outbound-controlled:",
        "worker-background-controlled:",
        "worker-webchat-ai-controlled:",
        "worker-handoff-snapshot-controlled:",
    )
    for service in required_services:
        if service not in text:
            raise PreflightError(f"compose_service_missing:{service[:-1]}")


def _placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    if "replace-with" in normalized or "placeholder" in normalized:
        return True
    return normalized in {
        "changeme",
        "change-me",
        "replace-me",
        "example-secret",
        "server-secret",
    }


def _require_secret(values: dict[str, str], key: str, *, minimum_length: int) -> None:
    value = values.get(key, "")
    if len(value) < minimum_length or _placeholder(value):
        raise PreflightError(f"secret_invalid:{key}")


def _validate_build_time(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = datetime.strptime(normalized, "%Y%m%dT%H%M%SZ")
    except ValueError as exc:
        raise PreflightError("build_time_invalid") from exc
    if parsed.year < 2025:
        raise PreflightError("build_time_invalid")
    return normalized


def _manifest_identity(manifest: dict) -> tuple[dict, dict]:
    if manifest.get("schema") != "nexus.osr.controlled-candidate-manifest.v1":
        raise PreflightError("manifest_schema_invalid")
    if manifest.get("status") != "pass":
        raise PreflightError("manifest_status_invalid")
    if manifest.get("decision") != "CONTROLLED_SERVER_CANDIDATE_PUBLISHED":
        raise PreflightError("manifest_decision_invalid")
    if manifest.get("release_class") != "controlled_server_deployment":
        raise PreflightError("manifest_release_class_invalid")
    candidate = manifest.get("candidate")
    if not isinstance(candidate, dict):
        raise PreflightError("manifest_candidate_invalid")
    safety = manifest.get("safety")
    if not isinstance(safety, dict):
        raise PreflightError("manifest_safety_invalid")
    expected_manifest_safety = {
        "production_ready": False,
        "full_osr_automation": "NO_GO",
        "issue_533_go": False,
        "deployment_performed": False,
        "external_effects_authorized": False,
        "provider_enabled": False,
        "real_outbound_enabled": False,
        "whatsapp_enabled": False,
        "speedaf_writes_enabled": False,
        "operations_dispatch_enabled": False,
    }
    for key, expected in expected_manifest_safety.items():
        if safety.get(key) != expected:
            raise PreflightError(f"manifest_safety_invalid:{key}")
    attestation = manifest.get("attestation")
    if not isinstance(attestation, dict):
        raise PreflightError("manifest_attestation_invalid")
    attestation_id = str(attestation.get("id") or "")
    attestation_url = str(attestation.get("url") or "")
    if not _ATTESTATION_ID.fullmatch(attestation_id):
        raise PreflightError("manifest_attestation_id_invalid")
    if not attestation_url.startswith("https://github.com/") or any(
        char in attestation_url for char in "\r\n\x00"
    ):
        raise PreflightError("manifest_attestation_url_invalid")
    if attestation.get("registry_provenance_pushed") is not True:
        raise PreflightError("manifest_attestation_not_pushed")
    return candidate, safety


def validate(
    *,
    env_path: Path,
    compose_path: Path,
    manifest_path: Path,
    expected_database_host: str | None,
    expected_database_port: int,
    expected_domain: str | None,
    check_host_paths: bool,
) -> dict[str, object]:
    values = _parse_env(env_path)
    _validate_compose(compose_path)
    manifest = _load_json(manifest_path)
    candidate, _ = _manifest_identity(manifest)

    image = values.get("CONTROLLED_IMAGE", "").lower()
    if not _DIGEST_IMAGE.fullmatch(image):
        raise PreflightError("controlled_image_not_digest_pinned")
    if image != candidate.get("registry_reference"):
        raise PreflightError("controlled_image_manifest_mismatch")
    registry_digest = str(candidate.get("registry_digest") or "")
    if not _SHA256.fullmatch(registry_digest):
        raise PreflightError("manifest_registry_digest_invalid")
    if not image.endswith("@" + registry_digest):
        raise PreflightError("manifest_registry_reference_invalid")
    local_image = str(candidate.get("local_image_id") or "")
    pull_image = str(candidate.get("registry_pull_image_id") or "")
    if not _SHA256.fullmatch(local_image) or pull_image != local_image:
        raise PreflightError("manifest_binary_identity_invalid")
    for key in ("config_digest",):
        if not _SHA256.fullmatch(str(candidate.get(key) or "")):
            raise PreflightError(f"manifest_candidate_field_invalid:{key}")
    for key in ("postgres_image_digest", "nginx_image_digest"):
        if not _DIGEST_REFERENCE.fullmatch(str(candidate.get(key) or "")):
            raise PreflightError(f"manifest_candidate_field_invalid:{key}")

    source = values.get("GIT_SHA", "").lower()
    frontend = values.get("FRONTEND_BUILD_SHA", "").lower()
    migration = values.get("EXPECTED_MIGRATION_HEAD", "")
    if not _SHA40.fullmatch(source):
        raise PreflightError("git_sha_invalid")
    if frontend != source or not _SHA40.fullmatch(frontend):
        raise PreflightError("frontend_sha_invalid")
    if not _MIGRATION.fullmatch(migration):
        raise PreflightError("migration_head_invalid")
    if source != candidate.get("source_sha"):
        raise PreflightError("source_manifest_mismatch")
    if frontend != candidate.get("frontend_build_sha"):
        raise PreflightError("frontend_manifest_mismatch")
    if migration != candidate.get("migration_revision"):
        raise PreflightError("migration_manifest_mismatch")
    if values.get("IMAGE_TAG") != image:
        raise PreflightError("image_tag_must_equal_digest_reference")

    build_time = _validate_build_time(values.get("BUILD_TIME", ""))
    app_version = values.get("APP_VERSION", "")
    if not _APP_VERSION.fullmatch(app_version):
        raise PreflightError("app_version_invalid")
    if build_time != candidate.get("build_time"):
        raise PreflightError("build_time_manifest_mismatch")
    if app_version != candidate.get("app_version"):
        raise PreflightError("app_version_manifest_mismatch")
    project = values.get("COMPOSE_PROJECT_NAME", "")
    if not _COMPOSE_PROJECT.fullmatch(project):
        raise PreflightError("compose_project_invalid")
    try:
        app_port = int(values.get("CONTROLLED_APP_PORT", ""))
    except ValueError as exc:
        raise PreflightError("controlled_app_port_invalid") from exc
    if not 1024 <= app_port <= 65535:
        raise PreflightError("controlled_app_port_invalid")

    if values.get("APP_ENV", "").lower() != "production":
        raise PreflightError("app_env_invalid")
    if values.get("READINESS_REQUIRE_RELEASE_METADATA", "").lower() != "true":
        raise PreflightError("readiness_metadata_gate_required")
    _require_secret(values, "SECRET_KEY", minimum_length=32)
    _require_secret(values, "RUNTIME_CONTRACT_SIGNING_SECRET", minimum_length=32)

    for key, expected in SAFE_CONTROLS.items():
        if values.get(key, "").lower() != expected:
            raise PreflightError(f"unsafe_control:{key}")
    for key, expected in OPTIONAL_DISABLED_CONTROLS.items():
        if key in values and values[key].lower() != expected:
            raise PreflightError(f"unsafe_optional_control:{key}")

    database_url = values.get("DATABASE_URL", "")
    normalized = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        parsed = urlsplit(normalized)
        parsed_port = parsed.port
    except ValueError as exc:
        raise PreflightError("database_url_invalid") from exc
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise PreflightError("database_url_invalid")
    if not parsed.username or not parsed.password or _placeholder(parsed.password):
        raise PreflightError("database_credentials_invalid")
    if expected_database_host and parsed.hostname != expected_database_host:
        raise PreflightError("database_host_mismatch")
    if parsed_port != expected_database_port:
        raise PreflightError("database_port_mismatch")
    if parsed.path.lstrip("/") != "nexusdesk":
        raise PreflightError("database_name_mismatch")

    if expected_domain:
        expected_origin = f"https://{expected_domain}"
        for key in ("ALLOWED_ORIGINS", "WEBCHAT_ALLOWED_ORIGINS"):
            origins = {
                item.strip().rstrip("/")
                for item in values.get(key, "").split(",")
                if item.strip()
            }
            if expected_origin not in origins:
                raise PreflightError(f"domain_origin_missing:{key}")

    path_keys = {
        "NEXUS_RUNTIME_SECRETS_HOST_PATH": "directory",
        "NEXUS_UPLOADS_HOST_PATH": "directory",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH": "directory",
        "AI_RUNTIME_TOKEN_HOST_PATH": "secret_file",
    }
    checked_paths: dict[str, str] = {}
    declared_paths: dict[str, Path] = {}
    for key, kind in path_keys.items():
        raw = values.get(key, "")
        if not raw.startswith("/"):
            raise PreflightError(f"host_path_invalid:{key}")
        path = Path(raw)
        declared_paths[key] = path
        checked_paths[key] = kind
        if not check_host_paths:
            continue
        if kind == "directory" and (not path.is_dir() or path.is_symlink()):
            raise PreflightError(f"host_directory_missing:{key}")
        if kind == "secret_file" and (not path.is_file() or path.is_symlink()):
            raise PreflightError(f"host_file_missing:{key}")
        if kind == "secret_file":
            size = path.stat().st_size
            if not 1 <= size <= MAX_SECRET_FILE_BYTES:
                raise PreflightError(f"host_secret_size_invalid:{key}")
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise PreflightError(f"host_secret_permissions_unsafe:{key}")
    if declared_paths["NEXUS_UPLOADS_HOST_PATH"] == declared_paths["NEXUS_UPLOAD_BACKUP_HOST_PATH"]:
        raise PreflightError("upload_and_backup_paths_must_differ")

    return {
        "schema": "nexus.osr.controlled-server-preflight.v1",
        "status": "pass",
        "source_sha": source,
        "frontend_build_sha": frontend,
        "migration_revision": migration,
        "build_time": build_time,
        "app_version": app_version,
        "registry_reference": image,
        "database_host": parsed.hostname,
        "database_port": parsed_port,
        "database_name": parsed.path.lstrip("/"),
        "expected_domain": expected_domain,
        "controlled_app_port": app_port,
        "host_paths_checked": check_host_paths,
        "declared_host_paths": checked_paths,
        "external_effects_enabled": False,
        "production_ready": False,
        "full_osr_automation": "NO_GO",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-database-host")
    parser.add_argument("--expected-database-port", type=int, required=True)
    parser.add_argument("--expected-domain")
    parser.add_argument("--check-host-paths", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = validate(
            env_path=args.env_file,
            compose_path=args.compose_file,
            manifest_path=args.manifest,
            expected_database_host=args.expected_database_host,
            expected_database_port=args.expected_database_port,
            expected_domain=args.expected_domain,
            check_host_paths=args.check_host_paths,
        )
    except (PreflightError, OSError, ValueError) as exc:
        print(f"controlled_server_preflight_error:{exc}", file=sys.stderr)
        return 2
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
