#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${CANDIDATE_ENV_FILE:-deploy/.env.candidate}"
COMPOSE_FILE="${CANDIDATE_COMPOSE_FILE:-deploy/docker-compose.candidate.yml}"
OUT_DIR="${OUT_DIR:-$(mktemp -d -t nexus-wa-candidate-runtime-audit.XXXXXX)}"
CHECK_RENDERED_CONFIG="${WA_CANDIDATE_AUDIT_RENDERED_CONFIG:-true}"
CHECK_RUNNING_CONTAINERS="${WA_CANDIDATE_AUDIT_RUNNING_CONTAINERS:-false}"

mkdir -p "$OUT_DIR"

is_true() {
  [[ "${1:-}" =~ ^(1|true|yes|on)$ ]]
}

require_file() {
  if [[ ! -f "$1" ]]; then
    echo "FAIL missing file: $1" >&2
    exit 2
  fi
}

require_file "$ENV_FILE"
require_file "$COMPOSE_FILE"

if grep -nEi 'openclaw' "$ENV_FILE" >"$OUT_DIR/openclaw-env-file-matches.txt"; then
  echo "FAIL candidate env contains retired OpenClaw markers" >&2
  cat "$OUT_DIR/openclaw-env-file-matches.txt" >&2
  exit 2
fi

python3 - "$ENV_FILE" "$OUT_DIR/env-contract.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

values: dict[str, str] = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    values[key.strip()] = value.strip()

expected = {
    "WHATSAPP_NATIVE_ENABLED": "true",
    "WHATSAPP_DISPATCH_MODE": "native_sidecar",
    "WHATSAPP_SIDECAR_URL": "http://whatsapp-sidecar-candidate:18793",
    "CANDIDATE_NEXUS_BACKEND_URL": "http://app-candidate:8080",
    "OUTBOUND_PROVIDER": "native",
    "EXTERNAL_CHANNEL_BRIDGE_ENABLED": "false",
    "EXTERNAL_CHANNEL_TRANSPORT": "disabled",
    "EXTERNAL_CHANNEL_DEPLOYMENT_MODE": "disabled",
    "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED": "false",
    "EXTERNAL_CHANNEL_SYNC_ENABLED": "false",
    "EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED": "false",
    "EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED": "false",
}

errors: list[str] = []
for key, expected_value in expected.items():
    actual = values.get(key)
    if actual != expected_value:
        errors.append(f"{key}={actual!r} expected={expected_value!r}")

for key, value in values.items():
    if key.upper().startswith("OPENCLAW") or "OPENCLAW" in value.upper():
        errors.append(f"retired_openclaw_marker:{key}")

result = {
    "ok": not errors,
    "checks": expected,
    "errors": errors,
    "env_file": str(path),
}
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if errors:
    raise SystemExit("\n".join(errors))
PY

if is_true "$CHECK_RENDERED_CONFIG"; then
  rendered="$OUT_DIR/docker-compose.rendered.yml"
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" config >"$rendered"
  if grep -nEi 'openclaw' "$rendered" >"$OUT_DIR/openclaw-rendered-compose-matches.txt"; then
    echo "FAIL rendered candidate compose contains retired OpenClaw markers" >&2
    cat "$OUT_DIR/openclaw-rendered-compose-matches.txt" >&2
    exit 2
  fi
  if grep -q 'NEXUS_BACKEND_URL: http://app:8080' "$rendered"; then
    echo "FAIL rendered candidate sidecar points at legacy app:8080" >&2
    exit 2
  fi
  grep -q 'OUTBOUND_PROVIDER: native' "$rendered"
  grep -q 'WHATSAPP_DISPATCH_MODE: native_sidecar' "$rendered"
  grep -q 'EXTERNAL_CHANNEL_BRIDGE_ENABLED: "false"' "$rendered"
fi

if is_true "$CHECK_RUNNING_CONTAINERS"; then
  project="${COMPOSE_PROJECT_NAME:-}"
  if [[ -z "$project" ]]; then
    echo "FAIL COMPOSE_PROJECT_NAME is required when WA_CANDIDATE_AUDIT_RUNNING_CONTAINERS=true" >&2
    exit 2
  fi
  for service in app-candidate worker-outbound-candidate whatsapp-sidecar-candidate; do
    container_id="$(COMPOSE_PROJECT_NAME="$project" docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps -q "$service")"
    if [[ -z "$container_id" ]]; then
      echo "FAIL missing running candidate container for service: $service" >&2
      exit 2
    fi
    env_out="$OUT_DIR/${service}.container-env.txt"
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id" >"$env_out"
    if grep -nEi 'openclaw' "$env_out" >"$OUT_DIR/${service}.openclaw-container-env-matches.txt"; then
      echo "FAIL running candidate container env contains retired OpenClaw markers: $service" >&2
      cat "$OUT_DIR/${service}.openclaw-container-env-matches.txt" >&2
      exit 2
    fi
  done
fi

echo "WHATSAPP_CANDIDATE_RUNTIME_AUDIT_PASS=true"
echo "evidence_dir=$OUT_DIR"
