#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/backend"

python scripts/validate_production_readiness.py || true
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
    if s.openclaw_transport != "mcp":
        missing.append("OPENCLAW_TRANSPORT=mcp")
    if s.openclaw_deployment_mode == "remote_gateway":
        if not s.openclaw_mcp_url:
            missing.append("OPENCLAW_MCP_URL")
        if not s.openclaw_mcp_token_file and not s.openclaw_mcp_password_file:
            missing.append("OPENCLAW_MCP_TOKEN_FILE or OPENCLAW_MCP_PASSWORD_FILE")
if missing:
    raise SystemExit("Preflight failed. Missing/invalid: " + ", ".join(missing))
print("Preflight OK")
PY
