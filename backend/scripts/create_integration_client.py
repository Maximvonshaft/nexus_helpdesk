from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth_service import hash_secret  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import IntegrationClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Create an integration client credential')
    parser.add_argument('--name', required=True)
    parser.add_argument('--key-id', default=None)
    parser.add_argument('--secret', default=None)
    parser.add_argument('--scopes', default='profile.read,task.write')
    parser.add_argument('--rate-limit', type=int, default=60)
    args = parser.parse_args()

    key_id = args.key_id or f"cli_{secrets.token_hex(6)}"
    secret = args.secret or secrets.token_urlsafe(24)

    db = SessionLocal()
    try:
        existing = db.query(IntegrationClient).filter(IntegrationClient.key_id == key_id).first()
        if existing:
            raise SystemExit(f'key_id already exists: {key_id}')
        client = IntegrationClient(
            name=args.name,
            key_id=key_id,
            secret_hash=hash_secret(secret),
            scopes_csv=args.scopes,
            rate_limit_per_minute=args.rate_limit,
            is_active=True,
        )
        db.add(client)
        db.commit()
        print(f'NAME={args.name}')
        print(f'KEY_ID={key_id}')
        print(f'SECRET={secret}')
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
