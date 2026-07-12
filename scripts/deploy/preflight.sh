#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/backend"

python scripts/validate_production_readiness.py
python - <<'PY'
from app.settings import get_settings
s = get_settings()
missing = []
if not s.jwt_secret_key:
    missing.append("SECRET_KEY")
if s.app_env == "production":
    if not s.database_url.startswith("postgresql"):
        missing.append("DATABASE_URL(postgresql)")
    if s.storage_backend == "s3":
        for key in ["S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY", "S3_SECRET_KEY"]:
            if not getattr(s, key.lower()):
                missing.append(key)
    if s.external_channel_transport != "disabled":
        missing.append("EXTERNAL_CHANNEL_TRANSPORT=disabled")
    if s.external_channel_deployment_mode != "disabled":
        missing.append("EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled")
    if s.external_channel_sync_enabled:
        missing.append("EXTERNAL_CHANNEL_SYNC_ENABLED=false")
    if s.external_channel_event_driver_enabled:
        missing.append("EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED=false")
if missing:
    raise SystemExit("Preflight failed. Missing/invalid: " + ", ".join(missing))
print("Preflight OK")
PY
