from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth_service import hash_secret  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import IntegrationClient, Tenant  # noqa: E402
from app.models_integration_scope import IntegrationClientScope  # noqa: E402
from app.utils.time import utc_now  # noqa: E402

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,119}$")
_SAFE_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_SAFE_SCOPE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")


def _read_secret_from_stdin() -> str:
    if sys.stdin.isatty():
        raise ValueError("piped_secret_required")
    secret = sys.stdin.readline().strip()
    if len(secret) < 24:
        raise ValueError("secret_value_too_short")
    return secret


def _validated_identity(name: str, key_id: str, scopes: str) -> tuple[str, str, str]:
    cleaned_name = " ".join(name.strip().split())
    cleaned_key_id = key_id.strip()
    cleaned_scopes = ",".join(
        item.strip() for item in scopes.split(",") if item.strip()
    )
    if not _SAFE_NAME.fullmatch(cleaned_name):
        raise ValueError("integration_client_name_invalid")
    if not _SAFE_KEY_ID.fullmatch(cleaned_key_id):
        raise ValueError("integration_client_key_id_invalid")
    scope_values = cleaned_scopes.split(",") if cleaned_scopes else []
    if not scope_values or any(not _SAFE_SCOPE.fullmatch(item) for item in scope_values):
        raise ValueError("integration_client_scopes_invalid")
    return cleaned_name, cleaned_key_id, cleaned_scopes


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Tenant-bound or explicit platform Integration credential "
            "from one secret line on stdin"
        )
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--key-id", default=None)
    parser.add_argument("--secret-stdin", action="store_true", required=True)
    parser.add_argument("--scopes", default=None)
    parser.add_argument("--rate-limit", type=int, default=60)
    scope_group = parser.add_mutually_exclusive_group(required=True)
    scope_group.add_argument("--tenant-key")
    scope_group.add_argument("--platform", action="store_true")
    args = parser.parse_args()

    scope_type = "platform" if args.platform else "tenant"
    default_scopes = (
        "platform.profile.read,platform.task.write"
        if scope_type == "platform"
        else "profile.read,task.write"
    )
    key_id = args.key_id or f"cli_{secrets.token_hex(6)}"
    try:
        name, key_id, scopes = _validated_identity(
            args.name,
            key_id,
            args.scopes or default_scopes,
        )
        secret = _read_secret_from_stdin()
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.rate_limit <= 10000:
        parser.error("rate_limit_out_of_range")

    scope_values = set(scopes.split(","))
    if scope_type == "platform" and any(
        not item.startswith("platform.") for item in scope_values
    ):
        parser.error("platform_integration_requires_platform_scopes")
    if scope_type == "tenant" and any(
        item.startswith("platform.") for item in scope_values
    ):
        parser.error("tenant_integration_cannot_receive_platform_scope")

    db = SessionLocal()
    try:
        existing = (
            db.query(IntegrationClient)
            .filter(IntegrationClient.key_id == key_id)
            .first()
        )
        if existing:
            raise SystemExit("integration_client_key_id_exists")

        tenant = None
        if scope_type == "tenant":
            tenant_key = str(args.tenant_key or "").strip().lower()
            tenant = (
                db.query(Tenant)
                .filter(
                    Tenant.tenant_key == tenant_key,
                    Tenant.is_active.is_(True),
                )
                .first()
            )
            if tenant is None:
                raise SystemExit("integration_client_tenant_not_found")

        client = IntegrationClient(
            name=name,
            key_id=key_id,
            secret_hash=hash_secret(secret),
            scopes_csv=scopes,
            rate_limit_per_minute=args.rate_limit,
            is_active=True,
        )
        db.add(client)
        db.flush()
        db.add(
            IntegrationClientScope(
                client_id=client.id,
                scope_type=scope_type,
                tenant_id=tenant.id if tenant is not None else None,
                assigned_by=None,
                assignment_source="create_integration_client_cli",
                assignment_version="nexus.integration-principal-scope.v1",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        db.commit()
        print(
            json.dumps(
                {
                    "status": "created",
                    "name": name,
                    "key_id": key_id,
                    "scope_type": scope_type,
                    "tenant_key": tenant.tenant_key if tenant is not None else None,
                    "scopes": sorted(scope_values),
                    "secret_delivery": "stdin",
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
