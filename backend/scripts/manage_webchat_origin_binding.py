#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import db_context
from app.model_registry import register_all_models
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.services.webchat_tenant_binding import normalize_public_origin
from app.utils.time import utc_now

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _country(value: str) -> str:
    normalized = value.strip().upper()
    if not _COUNTRY_RE.fullmatch(normalized):
        raise SystemExit("invalid_country_code")
    return normalized


def _safe_row(row: WebchatPublicOriginBinding) -> dict[str, object]:
    return {
        "id": row.id,
        "origin": row.normalized_origin,
        "tenant_hash": _hash(row.tenant_key),
        "country_code": row.country_code,
        "channel_key": row.channel_key,
        "display_name": row.display_name,
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage server-owned public WebChat origin bindings.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--include-disabled", action="store_true")

    upsert = sub.add_parser("upsert")
    upsert.add_argument("--origin", required=True)
    upsert.add_argument("--tenant", required=True)
    upsert.add_argument("--country", required=True)
    upsert.add_argument("--channel", required=True)
    upsert.add_argument("--display-name")

    disable = sub.add_parser("disable")
    disable.add_argument("--origin", required=True)

    args = parser.parse_args()
    register_all_models()

    with db_context() as db:
        if args.command == "list":
            query = db.query(WebchatPublicOriginBinding)
            if not args.include_disabled:
                query = query.filter(WebchatPublicOriginBinding.is_active.is_(True))
            rows = query.order_by(WebchatPublicOriginBinding.normalized_origin.asc()).limit(500).all()
            print(json.dumps({"bindings": [_safe_row(row) for row in rows]}, sort_keys=True))
            return 0

        origin = normalize_public_origin(args.origin)
        if origin is None:
            raise SystemExit("origin_required")
        row = (
            db.query(WebchatPublicOriginBinding)
            .filter(WebchatPublicOriginBinding.normalized_origin == origin)
            .first()
        )
        if args.command == "disable":
            if row is None:
                raise SystemExit("binding_not_found")
            row.is_active = False
            row.updated_at = utc_now()
            db.flush()
            print(json.dumps(_safe_row(row), sort_keys=True))
            return 0

        country_code = _country(args.country)
        if row is None:
            row = WebchatPublicOriginBinding(
                normalized_origin=origin,
                tenant_key=args.tenant.strip(),
                country_code=country_code,
                channel_key=args.channel.strip(),
                display_name=(args.display_name or "").strip() or None,
                is_active=True,
            )
            db.add(row)
        else:
            row.tenant_key = args.tenant.strip()
            row.country_code = country_code
            row.channel_key = args.channel.strip()
            row.display_name = (args.display_name or "").strip() or None
            row.is_active = True
            row.updated_at = utc_now()
        db.flush()
        print(json.dumps(_safe_row(row), sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
