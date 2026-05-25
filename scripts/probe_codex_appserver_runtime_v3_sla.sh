#!/usr/bin/env bash
set -Eeuo pipefail

BRIDGE_URL="${CODEX_APP_SERVER_BRIDGE_URL:-http://127.0.0.1:18794/reply}"
READYZ_URL="${CODEX_APPSERVER_SLA_READYZ_URL:-http://127.0.0.1:18794/readyz}"
TOKEN_FILE="${CODEX_APP_SERVER_TOKEN_FILE:-/run/nexus/codex_app_server_bridge_token}"
ORIGIN="${CODEX_APP_SERVER_PROBE_ORIGIN:-https://nexusdesk.local}"
OUT_DIR="${OUT_DIR:-/tmp/nexus_codex_runtime_v3_sla_$(date -u '+%Y%m%dT%H%M%SZ')}"
SEQUENTIAL="${CODEX_APPSERVER_SLA_SEQUENTIAL:-20}"
PARALLEL_6="${CODEX_APPSERVER_SLA_PARALLEL_6:-6}"
PARALLEL_12="${CODEX_APPSERVER_SLA_PARALLEL_12:-12}"
ALLOW_CONTROLLED_QUEUE_TIMEOUT="${CODEX_APPSERVER_SLA_ALLOW_CONTROLLED_QUEUE_TIMEOUT:-true}"
RESTART_RUNTIME="${CODEX_APPSERVER_SLA_RESTART_RUNTIME:-false}"
COMPOSE_FILE="${CODEX_APPSERVER_SLA_COMPOSE_FILE:-deploy/docker-compose.server.yml}"
PROFILE_MATRIX="${CODEX_APPSERVER_SLA_PROFILE_MATRIX:-current,current,current,current,current,current,current}"
VALID_TOKEN="${NEXUS_CODEX_ACCESS_TOKEN:-${CODEX_APPSERVER_VALID_ACCESS_TOKEN:-}}"

mkdir -p "$OUT_DIR"

if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "SLA_STATUS=SKIPPED token_file_unreadable"
  exit 1
fi
if [[ -z "$VALID_TOKEN" ]]; then
  echo "SLA_STATUS=SKIPPED valid_token_missing"
  echo "Set NEXUS_CODEX_ACCESS_TOKEN or CODEX_APPSERVER_VALID_ACCESS_TOKEN in the controlled server environment."
  exit 1
fi

BRIDGE_TOKEN="$(sed -e 's/^Bearer[[:space:]]*//I' "$TOKEN_FILE" | head -n1)"

sanitize_name() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}

payload() {
  local token="$1"
  local session="$2"
  cat <<JSON
{"login":{"type":"chatgptAuthTokens","accessToken":"$token","chatgptAccountId":"sla-account","chatgptPlanType":"plus"},"body":"Hello, please reply with strict JSON.","messages":[],"contract":"speedaf_webchat_fast_reply_v1","tracking_fact_summary":null,"tracking_fact_evidence_present":false,"tenant_id":"default","channel_key":"website","session_id":"$session"}
JSON
}

apply_profile() {
  local name="$1"
  local model="$2"
  local effort="$3"
  local service_tier="$4"
  local max_concurrency="$5"
  local queue_timeout="$6"
  local reply_timeout="$7"
  if [[ "$RESTART_RUNTIME" != "true" ]]; then
    return
  fi
  [[ "$model" == "current" ]] || export CODEX_APPSERVER_MODEL="$model"
  [[ "$effort" == "current" ]] || export CODEX_APPSERVER_REASONING_EFFORT="$effort"
  [[ "$service_tier" == "current" ]] || export CODEX_APPSERVER_SERVICE_TIER="$service_tier"
  [[ "$max_concurrency" == "current" ]] || export CODEX_APPSERVER_MAX_CONCURRENCY="$max_concurrency"
  [[ "$queue_timeout" == "current" ]] || export CODEX_APPSERVER_QUEUE_TIMEOUT_MS="$queue_timeout"
  [[ "$reply_timeout" == "current" ]] || export CODEX_APPSERVER_REPLY_TIMEOUT_MS="$reply_timeout"
  export CODEX_APP_SERVER_RUNTIME_BACKEND=node_appserver
  docker compose -f "$COMPOSE_FILE" --profile codex-app-server up -d codex-appserver-runtime codex-app-server-bridge >/dev/null
  for _ in $(seq 1 45); do
    if curl -fsS "$READYZ_URL" >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  echo "SLA_STATUS=FAILED profile_readyz_timeout profile=$name"
  exit 1
}

write_profile_meta() {
  local dir="$1"
  local name="$2"
  local model="$3"
  local effort="$4"
  local service_tier="$5"
  local max_concurrency="$6"
  local queue_timeout="$7"
  local reply_timeout="$8"
  {
    printf 'profile=%s\n' "$name"
    printf 'model=%s\n' "$model"
    printf 'reasoning_effort=%s\n' "$effort"
    printf 'service_tier=%s\n' "$service_tier"
    printf 'max_concurrency=%s\n' "$max_concurrency"
    printf 'queue_timeout_ms=%s\n' "$queue_timeout"
    printf 'reply_timeout_ms=%s\n' "$reply_timeout"
  } > "$dir/profile.txt"
}

run_one() {
  local profile="$1"
  local phase="$2"
  local idx="$3"
  local token="$4"
  local profile_dir="$OUT_DIR/$profile"
  local id="${phase}_${idx}"
  local body="$profile_dir/response_${id}.json"
  local headers="$profile_dir/headers_${id}.txt"
  local meta="$profile_dir/meta_${id}.txt"
  local start end code elapsed
  start="$(date +%s%3N)"
  code="$(payload "$token" "sla-${profile}-${phase}-${idx}" | curl -sS -D "$headers" -o "$body" -w '%{http_code}' \
    -H "Authorization: Bearer $BRIDGE_TOKEN" \
    -H "Content-Type: application/json" \
    -H "Origin: $ORIGIN" \
    --data-binary @- \
    "$BRIDGE_URL" || true)"
  end="$(date +%s%3N)"
  elapsed=$((end-start))
  {
    printf 'profile=%s\n' "$profile"
    printf 'phase=%s\n' "$phase"
    printf 'idx=%s\n' "$idx"
    printf 'code=%s\n' "$code"
    printf 'elapsed_ms=%s\n' "$elapsed"
  } > "$meta"
}

IFS=';' read -r -a PROFILE_SPECS <<< "$PROFILE_MATRIX"
for spec in "${PROFILE_SPECS[@]}"; do
  IFS=',' read -r raw_name model effort service_tier max_concurrency queue_timeout reply_timeout <<< "$spec"
  raw_name="${raw_name:-current}"
  model="${model:-current}"
  effort="${effort:-current}"
  service_tier="${service_tier:-current}"
  max_concurrency="${max_concurrency:-current}"
  queue_timeout="${queue_timeout:-current}"
  reply_timeout="${reply_timeout:-current}"
  profile="$(sanitize_name "$raw_name")"
  profile_dir="$OUT_DIR/$profile"
  mkdir -p "$profile_dir"
  write_profile_meta "$profile_dir" "$raw_name" "$model" "$effort" "$service_tier" "$max_concurrency" "$queue_timeout" "$reply_timeout"
  apply_profile "$raw_name" "$model" "$effort" "$service_tier" "$max_concurrency" "$queue_timeout" "$reply_timeout"

  for i in $(seq 1 "$SEQUENTIAL"); do
    run_one "$profile" "sequential" "$i" "$VALID_TOKEN"
  done

  for i in $(seq 1 "$PARALLEL_6"); do
    run_one "$profile" "parallel_6" "$i" "$VALID_TOKEN" &
  done
  wait

  for i in $(seq 1 "$PARALLEL_12"); do
    run_one "$profile" "parallel_12" "$i" "$VALID_TOKEN" &
  done
  wait

  run_one "$profile" "dummy_negative" "1" "dummy-token"
done

python - "$OUT_DIR" "$VALID_TOKEN" "$ALLOW_CONTROLLED_QUEUE_TIMEOUT" <<'PY'
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

out = Path(sys.argv[1])
valid_token = sys.argv[2]
allow_controlled_queue = sys.argv[3].strip().lower() in {"1", "true", "yes", "on"}

secret_patterns = [
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}"),
    re.compile(r"Bearer [A-Za-z0-9._-]{20,}", re.I),
    re.compile(r"accessToken|refreshToken|Authorization", re.I),
]
if valid_token:
    secret_patterns.append(re.compile(re.escape(valid_token)))

def parse_kv(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    return fields

def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * p) - 1)]

def stats(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    return {
        "min_ms": ordered[0] if ordered else 0,
        "p50_ms": percentile(ordered, 0.50),
        "p95_ms": percentile(ordered, 0.95),
        "p99_ms": percentile(ordered, 0.99),
        "max_ms": ordered[-1] if ordered else 0,
    }

def header_values(path: Path, name: str) -> list[str]:
    if not path.exists():
        return []
    prefix = name.lower() + ":"
    values = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith(prefix):
            values.append(line.split(":", 1)[1].strip())
    return values

profiles: dict[str, dict] = {}
for profile_dir in sorted(path for path in out.iterdir() if path.is_dir()):
    profile = profile_dir.name
    profile_meta = parse_kv(profile_dir / "profile.txt") if (profile_dir / "profile.txt").exists() else {}
    phase_elapsed: dict[str, list[int]] = defaultdict(list)
    phase_success: Counter[str] = Counter()
    phase_error: Counter[str] = Counter()
    error_taxonomy: Counter[str] = Counter()
    error_stage_taxonomy: Counter[str] = Counter()
    phase_error_taxonomy: dict[str, Counter[str]] = defaultdict(Counter)
    backend_seen: set[str] = set()
    reply_source_seen: set[str] = set()
    token_leakage_count = 0
    dummy_token_success_count = 0
    dummy_assistant_success_count = 0
    invalid_json_count = 0

    for meta_path in sorted(profile_dir.glob("meta_*.txt")):
        meta = parse_kv(meta_path)
        phase = meta.get("phase", "unknown")
        idx = meta.get("idx", "0")
        code = int(meta.get("code", "0") or "0")
        elapsed = int(meta.get("elapsed_ms", "0") or "0")
        body_path = profile_dir / f"response_{phase}_{idx}.json"
        headers_path = profile_dir / f"headers_{phase}_{idx}.txt"
        body_text = body_path.read_text(encoding="utf-8", errors="replace") if body_path.exists() else ""
        headers_text = headers_path.read_text(encoding="utf-8", errors="replace") if headers_path.exists() else ""
        for pattern in secret_patterns:
            if pattern.search(body_text) or pattern.search(headers_text):
                token_leakage_count += 1
                break
        for value in header_values(headers_path, "X-Nexus-Codex-Backend"):
            if value:
                backend_seen.add(value)
        try:
            body = json.loads(body_text) if body_text else {}
        except Exception:
            body = {}
            invalid_json_count += 1
        if isinstance(body, dict):
            if isinstance(body.get("reply_source"), str):
                reply_source_seen.add(body["reply_source"])
            if isinstance(body.get("runtime_backend"), str):
                backend_seen.add(body["runtime_backend"])
            if isinstance(body.get("backend"), str):
                backend_seen.add(body["backend"])
        if phase != "dummy_negative":
            phase_elapsed[phase].append(elapsed)
            if code == 200:
                phase_success[phase] += 1
            else:
                phase_error[phase] += 1
                if isinstance(body, dict):
                    err = str(body.get("error") or body.get("upstream_error") or f"http_{code}")
                    stage = str(body.get("error_stage") or "unknown")
                else:
                    err = f"http_{code}"
                    stage = "unknown"
                error_taxonomy[err] += 1
                error_stage_taxonomy[stage] += 1
                phase_error_taxonomy[phase][err] += 1
        else:
            if code == 200:
                dummy_token_success_count += 1
                if isinstance(body, dict) and str(body.get("reply") or "").strip():
                    dummy_assistant_success_count += 1

    all_elapsed = [value for values in phase_elapsed.values() for value in values]
    phase_stats = {phase: stats(values) for phase, values in sorted(phase_elapsed.items())}
    phase_counts = {
        phase: {
            "success_count": phase_success[phase],
            "error_count": phase_error[phase],
            "error_taxonomy": dict(sorted(phase_error_taxonomy[phase].items())),
        }
        for phase in sorted(set(phase_elapsed) | set(phase_success) | set(phase_error))
    }
    parallel_6_ok = phase_success["parallel_6"] == 6 and phase_error["parallel_6"] == 0 and phase_stats.get("parallel_6", {}).get("p95_ms", 0) <= 8000
    parallel_12_total = phase_success["parallel_12"] + phase_error["parallel_12"]
    parallel_12_errors = phase_error_taxonomy["parallel_12"]
    parallel_12_all_success = parallel_12_total == 12 and phase_error["parallel_12"] == 0
    parallel_12_controlled_queue = (
        allow_controlled_queue
        and parallel_12_total == 12
        and phase_error["parallel_12"] > 0
        and set(parallel_12_errors) <= {"codex_queue_timeout"}
    )
    hard_fail = (
        token_leakage_count > 0
        or dummy_token_success_count > 0
        or dummy_assistant_success_count > 0
        or invalid_json_count > 0
        or not parallel_6_ok
        or not (parallel_12_all_success or parallel_12_controlled_queue)
        or stats(all_elapsed)["max_ms"] > 12000
        or "codex_upstream_http_error" in error_taxonomy
        or "codex_model_error" in error_taxonomy
    )
    verdict = "PASS" if not hard_fail and parallel_12_all_success else "PASS_CONTROLLED_QUEUE" if not hard_fail else "FAIL"
    profiles[profile] = {
        **stats(all_elapsed),
        "profile": profile_meta,
        "success_count": sum(phase_success.values()),
        "error_count": sum(phase_error.values()),
        "timeout_count": sum(count for err, count in error_taxonomy.items() if "timeout" in err),
        "invalid_json_count": invalid_json_count,
        "token_leakage_count": token_leakage_count,
        "dummy_token_success_count": dummy_token_success_count,
        "dummy_assistant_success_count": dummy_assistant_success_count,
        "error_taxonomy_summary": dict(sorted(error_taxonomy.items())),
        "error_stage_summary": dict(sorted(error_stage_taxonomy.items())),
        "phase_counts": phase_counts,
        "phase_latency": phase_stats,
        "backend_seen": sorted(backend_seen),
        "reply_source_seen": sorted(reply_source_seen),
        "parallel_12_controlled_queue": parallel_12_controlled_queue,
        "verdict": verdict,
    }

passing = [
    (name, data)
    for name, data in profiles.items()
    if data["verdict"] in {"PASS", "PASS_CONTROLLED_QUEUE"}
]
recommended = None
if passing:
    recommended = min(
        passing,
        key=lambda item: (
            item[1]["phase_latency"].get("parallel_6", {}).get("p95_ms", 999999),
            item[1]["error_count"],
            item[1]["p95_ms"],
        ),
    )[0]

summary = {
    "profiles": profiles,
    "recommended_profile": recommended,
    "verdict": "PASS" if recommended else "FAIL",
}
print(json.dumps(summary, indent=2, sort_keys=True))
(out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
sys.exit(0 if recommended else 1)
PY
