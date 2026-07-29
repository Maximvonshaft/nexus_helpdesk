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
from app.models_job_scope import IntegrationClientScope  # noqa: E402

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


def _validated_identity(
    name: str,
    key_id: str,
    scopes: str,
) -> tuple[str, str, str]:
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
    if not scope_values or any(
        not _SAFE_SCOPE.fullmatch(item) for item in scope_values
    ):
        raise ValueError("integration_client_scopes_invalid")
    return cleaned_name, cleaned_key_id, cleaned_scopes


def _principal_scope(args, db) -> tuple[str, int | None, str]:  # noqa: ANN001
    if args.platform:
        scopes = {item.strip() for item in args.scopes.split(",") if item.strip()}
        if not scopes or any(not item.startswith("platform.") for item in scopes):
            raise ValueError("platform_client_requires_platform_scopes")
        return "platform", None, "explicit_platform_cli"

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
        raise ValueError("integration_client_tenant_not_found")
    scopes = {item.strip() for item in args.scopes.split(",") if item.strip()}
    if any(item.startswith("platform.") for item in scopes):
        raise ValueError("tenant_client_cannot_hold_platform_scope")
    return "tenant", int(tenant.id), "explicit_tenant_cli"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create an explicitly scoped Integration client credential from "
            "one secret line on stdin"
        )
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--key-id", default=None)
    parser.add_argument("--secret-stdin", action="store_true", required=True)
    principal = parser.add_mutually_exclusive_group(required=True)
    principal.add_argument("--tenant-key")
    principal.add_argument("--platform", action="store_true")
    parser.add_argument("--scopes", default="profile.read,task.write")
    parser.add_argument("--rate-limit", type=int, default=60)
    args = parser.parse_args()

    key_id = args.key_id or f"cli_{secrets.token_hex(6)}"
    try:
        name, key_id, scopes = _validated_identity(
            args.name,
            key_id,
            args.scopes,
        )
        secret = _read_secret_from_stdin()
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.rate_limit <= 10000:
        parser.error("rate_limit_out_of_range")

    db = SessionLocal()
    try:
        try:
            scope_type, tenant_id, assignment_source = _principal_scope(args, db)
        except ValueError as exc:
            parser.error(str(exc))
        existing = (
            db.query(IntegrationClient)
            .filter(
                (IntegrationClient.key_id == key_id)
                | (IntegrationClient.name == name)
            )
            .first()
        )
        if existing is not None:
            raise SystemExit("integration_client_identity_exists")
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
                client_id=int(client.id),
                scope_type=scope_type,
                tenant_id=tenant_id,
                assignment_source=assignment_source,
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
                    "tenant_id": tenant_id,
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
