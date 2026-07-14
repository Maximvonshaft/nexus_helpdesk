#!/usr/bin/env python3
"""Generate a fail-closed isolated RC environment file for one exact source SHA."""

from __future__ import annotations

import argparse
import ast
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REVISION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_MISSING = object()


def _normalize_origin(value: str) -> str:
    text = value.strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("RC origin must be HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("RC origin must not contain credentials, query or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("RC origin must be a root/origin URL")
    if parsed.scheme == "http" and parsed.hostname.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("HTTP RC origin is allowed only on loopback")
    port = parsed.port
    host = f"[{parsed.hostname.lower()}]" if ":" in parsed.hostname else parsed.hostname.lower()
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}"


def _assignment_literal(tree: ast.Module, name: str):
    value_node: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value_node = node.value
    if value_node is None:
        return _MISSING
    try:
        return ast.literal_eval(value_node)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ValueError(f"alembic_{name}_invalid") from exc


def _normalize_down_revisions(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        values = tuple(value)
    else:
        raise ValueError("alembic_down_revision_invalid")
    if not values or any(not REVISION_RE.fullmatch(item) for item in values):
        raise ValueError("alembic_down_revision_invalid")
    return values


def _validate_revision_graph(revisions: dict[str, tuple[str, ...]], head: str) -> None:
    state: dict[str, int] = {}
    visited: set[str] = set()

    def visit(revision: str) -> None:
        current = state.get(revision, 0)
        if current == 1:
            raise ValueError("alembic_graph_cycle")
        if current == 2:
            return
        state[revision] = 1
        for parent in revisions[revision]:
            visit(parent)
        state[revision] = 2
        visited.add(revision)

    visit(head)
    if visited != set(revisions):
        raise ValueError("alembic_graph_unreachable")


def discover_alembic_head(versions_dir: Path) -> str:
    if not versions_dir.is_dir():
        raise ValueError("alembic_versions_dir_missing")

    revisions: dict[str, tuple[str, ...]] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ValueError("alembic_revision_file_invalid") from exc
        revision = _assignment_literal(tree, "revision")
        down_revision = _assignment_literal(tree, "down_revision")
        if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
            raise ValueError("alembic_revision_invalid")
        if down_revision is _MISSING:
            raise ValueError("alembic_down_revision_missing")
        if revision in revisions:
            raise ValueError("alembic_revision_duplicate")
        revisions[revision] = _normalize_down_revisions(down_revision)

    if not revisions:
        raise ValueError("alembic_revision_set_empty")

    referenced = {parent for parents in revisions.values() for parent in parents}
    unknown = sorted(referenced - set(revisions))
    if unknown:
        raise ValueError("alembic_down_revision_unknown")

    heads = sorted(set(revisions) - referenced)
    if len(heads) != 1:
        raise ValueError("alembic_head_count_invalid")
    head = heads[0]
    _validate_revision_graph(revisions, head)
    return head


def build_values(
    *,
    source_sha: str,
    compose_project: str,
    origin: str,
    expected_migration_head: str,
) -> dict[str, str]:
    if not SHA_RE.fullmatch(source_sha):
        raise ValueError("source SHA must be exact lowercase 40-character Git SHA")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", compose_project):
        raise ValueError("invalid Compose project name")
    if not REVISION_RE.fullmatch(expected_migration_head):
        raise ValueError("expected migration head must be a bounded revision identifier")
    normalized_origin = _normalize_origin(origin)
    pg_password = secrets.token_urlsafe(24)
    jwt_secret = secrets.token_urlsafe(48)
    contract_secret = secrets.token_urlsafe(48)
    admin_password = secrets.token_urlsafe(24)
    image_tag = f"nexusdesk/helpdesk:rc-test-{source_sha}"
    build_time = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "COMPOSE_PROJECT_NAME": compose_project,
        "RC_SOURCE_SHA": source_sha,
        "RC_IMAGE_TAG": image_tag,
        "IMAGE_TAG": image_tag,
        "RC_POSTGRES_IMAGE": "pgvector/pgvector:pg16",
        "RC_NGINX_IMAGE": "nginx:1.27-alpine",
        "GIT_SHA": source_sha,
        "BUILD_TIME": build_time,
        "APP_VERSION": f"rc-test-{source_sha[:12]}",
        "EXPECTED_MIGRATION_HEAD": expected_migration_head,
        "READINESS_REQUIRE_RELEASE_METADATA": "true",
        "FRONTEND_BUILD_SHA": source_sha,
        "RC_APP_PORT": "18083",
        "RC_BASE_URL": normalized_origin,
        "RC_PUBLIC_ORIGIN": normalized_origin,
        "RC_TEST_TENANT_KEY": "rc-test",
        "RC_TEST_CHANNEL_KEY": "website",
        "RC_TEST_DISPLAY_NAME": "RC-Test-Website",
        "POSTGRES_DB": "nexus_rc",
        "POSTGRES_USER": "nexus_rc",
        "POSTGRES_PASSWORD": pg_password,
        "DATABASE_URL": f"postgresql+psycopg://nexus_rc:{pg_password}@postgres-rc:5432/nexus_rc",
        "DATABASE_ECHO": "false",
        "APP_ENV": "production",
        "TENANT_RUNTIME_AUTHORITY_MODE": "enforce",
        "SECRET_KEY": jwt_secret,
        "RUNTIME_CONTRACT_SIGNING_SECRET": contract_secret,
        "JWT_ISSUER": "nexusdesk-rc-test",
        "JWT_AUDIENCE": "nexusdesk-rc-test-users",
        "ACCESS_TOKEN_EXPIRE_HOURS": "1",
        "AUTO_INIT_DB": "false",
        "SEED_DEMO_DATA": "false",
        "ALLOW_DEV_AUTH": "false",
        "ALLOW_LEGACY_INTEGRATION_API_KEY": "false",
        "ALLOWED_ORIGINS": normalized_origin,
        "TRUSTED_PROXY_IPS": "",
        "WEBCHAT_ALLOWED_ORIGINS": normalized_origin,
        "WEBCHAT_ALLOW_NO_ORIGIN": "false",
        "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT": "false",
        "WEBCHAT_RATE_LIMIT_BACKEND": "database",
        "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
        "WEBCHAT_AI_ENABLED": "false",
        "WEBCHAT_AI_RECONCILER_ENABLED": "false",
        "WEBCHAT_WS_ENABLED": "false",
        "WEBCHAT_WS_PUBLIC_ENABLED": "false",
        "WEBCHAT_WS_ADMIN_ENABLED": "false",
        "WEBCHAT_WS_BROKER": "database",
        "WEBCHAT_VOICE_ENABLED": "false",
        "WEBCALL_AI_PRODUCTION_ENABLED": "false",
        "WEBCALL_AI_AGENT_ENABLED": "false",
        "STORAGE_BACKEND": "local",
        "UPLOAD_ROOT": "/app/backend/uploads",
        "LOCAL_STORAGE_BACKUP_REQUIRED": "true",
        "LOCAL_STORAGE_BACKUP_PATH": "/var/backups/nexusdesk/uploads",
        "LOCAL_STORAGE_BACKUP_ACKNOWLEDGED": "true",
        "REQUIRE_REMOTE_STORAGE_IN_PRODUCTION": "false",
        "KNOWLEDGE_RUNTIME_VERSION": "legacy",
        "KNOWLEDGE_EMBEDDINGS_ENABLED": "false",
        "KNOWLEDGE_EMBEDDING_PROVIDER": "deterministic_hash",
        "WEBCHAT_KNOWLEDGE_REPLY_MODE": "deterministic_direct_answer",
        "KNOWLEDGE_VECTOR_FALLBACK_ALLOWED": "true",
        "PROVIDER_RUNTIME_ENABLED": "false",
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER": "private_ai_runtime",
        "PROVIDER_RUNTIME_FALLBACK_PROVIDERS": "[]",
        "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
        "PROVIDER_RUNTIME_KILL_SWITCH": "true",
        "PRIVATE_AI_RUNTIME_ENABLED": "false",
        "ENABLE_OUTBOUND_DISPATCH": "false",
        "OUTBOUND_PROVIDER": "disabled",
        "OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED": "false",
        "WHATSAPP_NATIVE_ENABLED": "false",
        "WHATSAPP_DISPATCH_MODE": "disabled",
        "EMAIL_MAILBOX_SYNC_ENABLED": "false",
        "ALLOW_LEGACY_ORIGINLESS_OUTBOUND": "false",
        "EXTERNAL_CHANNEL_DEPLOYMENT_MODE": "disabled",
        "EXTERNAL_CHANNEL_TRANSPORT": "disabled",
        "EXTERNAL_CHANNEL_SYNC_ENABLED": "false",
        "EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED": "false",
        "EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED": "false",
        "EXTERNAL_CHANNEL_BRIDGE_ENABLED": "false",
        "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED": "false",
        "WEBCHAT_TRACKING_FACT_LOOKUP_ENABLED": "false",
        "WEBCHAT_TRACKING_FACT_SOURCE": "speedaf_api",
        "SPEEDAF_MCP_ENABLED": "false",
        "SPEEDAF_TRACK_QUERY_ENABLED": "false",
        "SPEEDAF_WORK_ORDER_CREATE_ENABLED": "false",
        "SPEEDAF_UPDATE_ADDRESS_ENABLED": "false",
        "SPEEDAF_CANCEL_ENABLED": "false",
        "SPEEDAF_VOICE_CALLBACK_ENABLED": "false",
        "OPERATIONS_DISPATCH_MODE": "disabled",
        "OPERATIONS_DISPATCH_ADAPTER": "disabled",
        "OPERATIONS_DISPATCH_TENANT_AUTHORITY_READY": "false",
        "METRICS_ENABLED": "false",
        "LOG_JSON": "true",
        "REQUIRE_PROMETHEUS_CLIENT_IN_PRODUCTION": "true",
        "WEB_CONCURRENCY": "2",
        "WEB_TIMEOUT": "30",
        "WORKER_POLL_SECONDS": "0.5",
        "WEBCHAT_AI_WORKER_POLL_SECONDS": "0.25",
        "WEBCHAT_AI_WORKER_BUSY_POLL_SECONDS": "0.05",
        "RC_TEST_ADMIN_USERNAME": "rc_admin",
        "RC_TEST_ADMIN_PASSWORD": admin_password,
    }


def write_env(path: Path, values: dict[str, str]) -> None:
    for key, value in values.items():
        if any(char in value for char in "\r\n\x00"):
            raise ValueError(f"unsafe control character in {key}")
        if any(char.isspace() for char in value):
            raise ValueError(f"unquoted whitespace is not allowed in shell-loadable RC value {key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        repo_root = Path(__file__).resolve().parents[2]
        expected_migration_head = discover_alembic_head(repo_root / "backend" / "alembic" / "versions")
        values = build_values(
            source_sha=args.source_sha,
            compose_project=args.compose_project,
            origin=args.origin,
            expected_migration_head=expected_migration_head,
        )
        write_env(args.output, values)
    except (OSError, ValueError) as exc:
        print(f"RC_ENV_GENERATED=false reason={exc}")
        return 2
    print("RC_ENV_GENERATED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
