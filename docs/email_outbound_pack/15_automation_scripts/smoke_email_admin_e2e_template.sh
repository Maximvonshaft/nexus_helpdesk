#!/usr/bin/env bash
set -euo pipefail

echo "DEPRECATED: use 15_automation_scripts/smoke_email_admin_e2e.sh instead." >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/smoke_email_admin_e2e.sh"
