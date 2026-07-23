#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/backend"

PRODUCTION_PROFILE="${PRODUCTION_PROFILE:-controlled}" \
  python scripts/validate_production_readiness.py
python - <<'PY'
from app.settings import get_settings

settings = get_settings()
missing = []
if not settings.jwt_secret_key:
    missing.append("SECRET_KEY")
if settings.app_env == "production":
    if not settings.database_url.startswith("postgresql"):
        missing.append("DATABASE_URL(postgresql)")
    if settings.storage_backend == "s3":
        for key in ("S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
            if not getattr(settings, key.lower()):
                missing.append(key)
if missing:
    raise SystemExit("Preflight failed. Missing/invalid: " + ", ".join(missing))
print("Preflight OK")
PY
