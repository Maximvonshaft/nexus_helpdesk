#!/usr/bin/env python3
"""Fail-closed preflight for the Swiss controlled-server deployment."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

MAX_FILE_BYTES = 2 * 1024 * 1024
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_IMAGE = re.compile(
    r"^ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$"
)
_MIGRATION = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")

SAFE_CONTROLS = {
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
    "EXTERNAL_CHANNEL_DEPLOYMENT_MODE": "disabled",
    "EXTERNAL_CHANNEL_TRANSPORT": "disabled",
    "EXTERNAL_CHANNEL_SYNC_ENABLED": "false",
    "EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED": "false",
    "EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED": "false",
    "EXTERNAL_CHANNEL_BRIDGE_ENABLED": "false",
    "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED": "false",
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


def validate(
    *,
    env_path: Path,
    compose_path: Path,
    manifest_path: Path,
    expected_database_host: str | None,
    expected_domain: str | None,
    check_host_paths: bool,
) -> dict[str, object]:
    values = _parse_env(env_path)
    _validate_compose(compose_path)
    manifest = _load_json(manifest_path)
    if manifest.get("schema") != "nexus.osr.controlled-candidate-manifest.v1":
        raise PreflightError("manifest_schema_invalid")
    if manifest.get("status") != "pass":
        raise PreflightError("manifest_status_invalid")
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
    }
    for key, expected in expected_manifest_safety.items():
        if safety.get(key) != expected:
            raise PreflightError(f"manifest_safety_invalid:{key}")

    image = values.get("CONTROLLED_IMAGE", "").lower()
    if not _DIGEST_IMAGE.fullmatch(image):
        raise PreflightError("controlled_image_not_digest_pinned")
    if image != candidate.get("registry_reference"):
        raise PreflightError("controlled_image_manifest_mismatch")
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
    if values.get("APP_ENV", "").lower() != "production":
        raise PreflightError("app_env_invalid")
    if values.get("READINESS_REQUIRE_RELEASE_METADATA", "").lower() != "true":
        raise PreflightError("readiness_metadata_gate_required")

    for key, expected in SAFE_CONTROLS.items():
        if values.get(key, "").lower() != expected:
            raise PreflightError(f"unsafe_control:{key}")

    database_url = values.get("DATABASE_URL", "")
    normalized = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise PreflightError("database_url_invalid")
    if expected_database_host and parsed.hostname != expected_database_host:
        raise PreflightError("database_host_mismatch")
    if parsed.path.lstrip("/") != "nexusdesk":
        raise PreflightError("database_name_mismatch")

    if expected_domain:
        expected_origin = f"https://{expected_domain}"
        for key in ("ALLOWED_ORIGINS", "WEBCHAT_ALLOWED_ORIGINS"):
            origins = {item.strip().rstrip("/") for item in values.get(key, "").split(",") if item.strip()}
            if expected_origin not in origins:
                raise PreflightError(f"domain_origin_missing:{key}")

    path_keys = {
        "NEXUS_UPLOADS_HOST_PATH": "directory",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH": "directory",
        "AI_RUNTIME_TOKEN_HOST_PATH": "file",
    }
    checked_paths: dict[str, str] = {}
    for key, kind in path_keys.items():
        raw = values.get(key, "")
        if not raw.startswith("/"):
            raise PreflightError(f"host_path_invalid:{key}")
        path = Path(raw)
        checked_paths[key] = kind
        if not check_host_paths:
            continue
        if kind == "directory" and not path.is_dir():
            raise PreflightError(f"host_directory_missing:{key}")
        if kind == "file" and (not path.is_file() or path.is_symlink()):
            raise PreflightError(f"host_file_missing:{key}")
        if kind == "file":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise PreflightError(f"host_secret_permissions_unsafe:{key}")

    registry_digest = str(candidate.get("registry_digest") or "")
    if not _SHA256.fullmatch(registry_digest):
        raise PreflightError("manifest_registry_digest_invalid")

    return {
        "schema": "nexus.osr.controlled-server-preflight.v1",
        "status": "pass",
        "source_sha": source,
        "frontend_build_sha": frontend,
        "migration_revision": migration,
        "registry_reference": image,
        "database_host": parsed.hostname,
        "database_name": parsed.path.lstrip("/"),
        "expected_domain": expected_domain,
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
