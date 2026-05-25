#!/usr/bin/env bash
set -Eeuo pipefail

BRIDGE_URL="${CODEX_APP_SERVER_BRIDGE_URL:-http://127.0.0.1:18794/reply}"
READYZ_URL="${CODEX_APPSERVER_SLA_READYZ_URL:-http://127.0.0.1:18794/readyz}"
TOKEN_FILE="${CODEX_APP_SERVER_TOKEN_FILE:-/run/nexus/codex_app_server_bridge_token}"
ORIGIN="${CODEX_APP_SERVER_PROBE_ORIGIN:-https://nexusdesk.local}"
OUT_DIR="${OUT_DIR:-/tmp/nexus_codex_runtime_v3_sla_$(date -u '+%Y%m%dT%H%M%SZ')}"
PYTHON_BIN="${PYTHON:-python3}"
SEQUENTIAL="${CODEX_APPSERVER_SLA_SEQUENTIAL:-20}"
PILOT_PARALLEL="${CODEX_APPSERVER_SLA_PILOT_PARALLEL:-6}"
PARALLEL_6="${CODEX_APPSERVER_SLA_PARALLEL_6:-6}"
PARALLEL_12="${CODEX_APPSERVER_SLA_PARALLEL_12:-12}"
ALLOW_CONTROLLED_QUEUE_TIMEOUT="${CODEX_APPSERVER_SLA_ALLOW_CONTROLLED_QUEUE_TIMEOUT:-true}"
RESTART_RUNTIME="${CODEX_APPSERVER_SLA_RESTART_RUNTIME:-false}"
COMPOSE_FILE="${CODEX_APPSERVER_SLA_COMPOSE_FILE:-deploy/docker-compose.server.yml}"
PROFILE_MATRIX="${CODEX_APPSERVER_SLA_PROFILE_MATRIX:-current,current,current,current,current,current,current,current}"
VALID_TOKEN="${NEXUS_CODEX_ACCESS_TOKEN:-${CODEX_APPSERVER_VALID_ACCESS_TOKEN:-}}"

mkdir -p "$OUT_DIR"

write_min_summary() {
  local kind="$1"
  local message="$2"
  if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    "$PYTHON_BIN" - "$OUT_DIR" "$kind" "$message" <<'PY' || true
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
kind = sys.argv[2]
message = sys.argv[3]
summary = {
    "profiles": {},
    "recommended_profile": None,
    "verdict": "FAIL",
    "failure_kind": kind,
    "script_error": [message] if kind == "script_error" else [],
    "runtime_error": [message] if kind == "runtime_error" else [],
    "model_sla_error": [message] if kind == "model_sla_error" else [],
}
out.mkdir(parents=True, exist_ok=True)
(out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
PY
  else
    printf '{"failure_kind":"script_error","script_error":["python_missing"],"runtime_error":[],"model_sla_error":[],"profiles":{},"recommended_profile":null,"verdict":"FAIL"}\n' > "$OUT_DIR/summary.json"
  fi
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  write_min_summary "script_error" "python_missing"
  exit 1
fi
if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "SLA_STATUS=SKIPPED token_file_unreadable"
  write_min_summary "script_error" "token_file_unreadable"
  exit 1
fi
if [[ -z "$VALID_TOKEN" ]]; then
  echo "SLA_STATUS=SKIPPED valid_token_missing"
  echo "Set NEXUS_CODEX_ACCESS_TOKEN or CODEX_APPSERVER_VALID_ACCESS_TOKEN in the controlled server environment."
  write_min_summary "script_error" "valid_token_missing"
  exit 1
fi

BRIDGE_TOKEN="$(sed -e 's/^Bearer[[:space:]]*//I' "$TOKEN_FILE" | head -n1)"

sanitize_name() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}

is_non_current_profile() {
  local value
  for value in "$@"; do
    if [[ -n "$value" && "$value" != "current" ]]; then
      return 0
    fi
  done
  return 1
}

payload() {
  local token="$1"
  local session="$2"
  cat <<JSON
{"login":{"type":"chatgptAuthTokens","accessToken":"$token","chatgptAccountId":"sla-account","chatgptPlanType":"plus"},"body":"Hello, please reply with strict JSON.","messages":[],"contract":"speedaf_webchat_fast_reply_v1","tracking_fact_summary":null,"tracking_fact_evidence_present":false,"tenant_id":"default","channel_key":"website","session_id":"$session"}
JSON
}

record_profile_error() {
  local dir="$1"
  local kind="$2"
  local message="$3"
  {
    printf 'failure_kind=%s\n' "$kind"
    printf '%s=%s\n' "$kind" "$message"
  } > "$dir/profile_error.txt"
}

apply_profile() {
  local dir="$1"
  local name="$2"
  local model="$3"
  local effort="$4"
  local service_tier="$5"
  local max_concurrency="$6"
  local queue_timeout="$7"
  local reply_timeout="$8"
  if [[ "$RESTART_RUNTIME" != "true" ]]; then
    if is_non_current_profile "$model" "$effort" "$service_tier" "$max_concurrency" "$queue_timeout" "$reply_timeout"; then
      record_profile_error "$dir" "script_error" "profile_requires_CODEX_APPSERVER_SLA_RESTART_RUNTIME_true"
      return 1
    fi
    return 0
  fi
  [[ "$model" == "current" ]] || export CODEX_APPSERVER_MODEL="$model"
  [[ "$effort" == "current" ]] || export CODEX_APPSERVER_REASONING_EFFORT="$effort"
  [[ "$service_tier" == "current" ]] || export CODEX_APPSERVER_SERVICE_TIER="$service_tier"
  [[ "$max_concurrency" == "current" ]] || export CODEX_APPSERVER_MAX_CONCURRENCY="$max_concurrency"
  [[ "$queue_timeout" == "current" ]] || export CODEX_APPSERVER_QUEUE_TIMEOUT_MS="$queue_timeout"
  [[ "$reply_timeout" == "current" ]] || export CODEX_APPSERVER_REPLY_TIMEOUT_MS="$reply_timeout"
  export CODEX_APP_SERVER_RUNTIME_BACKEND=node_appserver
  if ! docker compose -f "$COMPOSE_FILE" --profile codex-app-server up -d codex-appserver-runtime codex-app-server-bridge >/dev/null; then
    record_profile_error "$dir" "runtime_error" "docker_compose_restart_failed"
    return 1
  fi
  for _ in $(seq 1 45); do
    if curl -fsS "$READYZ_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  record_profile_error "$dir" "runtime_error" "profile_readyz_timeout"
  return 1
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
  local pilot_parallel="$9"
  {
    printf 'profile=%s\n' "$name"
    printf 'model=%s\n' "$model"
    printf 'reasoning_effort=%s\n' "$effort"
    printf 'service_tier=%s\n' "$service_tier"
    printf 'max_concurrency=%s\n' "$max_concurrency"
    printf 'queue_timeout_ms=%s\n' "$queue_timeout"
    printf 'reply_timeout_ms=%s\n' "$reply_timeout"
    printf 'pilot_parallel=%s\n' "$pilot_parallel"
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

run_parallel_phase() {
  local profile="$1"
  local phase="$2"
  local count="$3"
  local token="$4"
  local i
  for i in $(seq 1 "$count"); do
    run_one "$profile" "$phase" "$i" "$token" &
  done
  wait
}

IFS=';' read -r -a PROFILE_SPECS <<< "$PROFILE_MATRIX"
for spec in "${PROFILE_SPECS[@]}"; do
  IFS=',' read -r raw_name model effort service_tier max_concurrency queue_timeout reply_timeout pilot_parallel <<< "$spec"
  raw_name="${raw_name:-current}"
  model="${model:-current}"
  effort="${effort:-current}"
  service_tier="${service_tier:-current}"
  max_concurrency="${max_concurrency:-current}"
  queue_timeout="${queue_timeout:-current}"
  reply_timeout="${reply_timeout:-current}"
  pilot_parallel="${pilot_parallel:-$max_concurrency}"
  if [[ -z "$pilot_parallel" || "$pilot_parallel" == "current" ]]; then
    pilot_parallel="$PILOT_PARALLEL"
  fi
  profile="$(sanitize_name "$raw_name")"
  profile_dir="$OUT_DIR/$profile"
  mkdir -p "$profile_dir"
  write_profile_meta "$profile_dir" "$raw_name" "$model" "$effort" "$service_tier" "$max_concurrency" "$queue_timeout" "$reply_timeout" "$pilot_parallel"
  if ! apply_profile "$profile_dir" "$raw_name" "$model" "$effort" "$service_tier" "$max_concurrency" "$queue_timeout" "$reply_timeout"; then
    continue
  fi

  for i in $(seq 1 "$SEQUENTIAL"); do
    run_one "$profile" "sequential" "$i" "$VALID_TOKEN"
  done
  run_parallel_phase "$profile" "parallel_${pilot_parallel}" "$pilot_parallel" "$VALID_TOKEN"
  if [[ "$pilot_parallel" != "$PARALLEL_6" && "$PARALLEL_6" != "$PARALLEL_12" ]]; then
    run_parallel_phase "$profile" "parallel_${PARALLEL_6}" "$PARALLEL_6" "$VALID_TOKEN"
  fi
  run_parallel_phase "$profile" "parallel_${PARALLEL_12}" "$PARALLEL_12" "$VALID_TOKEN"
  run_one "$profile" "dummy_negative" "1" "dummy-token"
done

"$PYTHON_BIN" - "$OUT_DIR" "$ALLOW_CONTROLLED_QUEUE_TIMEOUT" "$SEQUENTIAL" "$PARALLEL_12" <<'PY'
import json
import math
import os
import re
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

out = Path(sys.argv[1])
allow_controlled_queue = sys.argv[2].strip().lower() in {"1", "true", "yes", "on"}
sequential_target = int(sys.argv[3])
overload_target = int(sys.argv[4])
valid_token = os.environ.get("NEXUS_CODEX_ACCESS_TOKEN") or os.environ.get("CODEX_APPSERVER_VALID_ACCESS_TOKEN") or ""

def write_summary(summary: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))

def parse_kv(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not path.exists():
        return fields
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

def main() -> tuple[dict, int]:
    secret_patterns = [
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}"),
        re.compile(r"Bearer [A-Za-z0-9._-]{20,}", re.I),
        re.compile(r"accessToken|refreshToken|Authorization", re.I),
    ]
    if valid_token:
        secret_patterns.append(re.compile(re.escape(valid_token)))

    profiles: dict[str, dict] = {}
    script_errors: list[str] = []
    runtime_errors: list[str] = []
    model_sla_errors: list[str] = []

    for profile_dir in sorted(path for path in out.iterdir() if path.is_dir()):
        profile = profile_dir.name
        profile_meta = parse_kv(profile_dir / "profile.txt")
        profile_error = parse_kv(profile_dir / "profile_error.txt")
        if profile_error:
            failure_kind = profile_error.get("failure_kind", "runtime_error")
            message = profile_error.get(failure_kind, "profile_failed")
            if failure_kind == "script_error":
                script_errors.append(f"{profile}:{message}")
            else:
                runtime_errors.append(f"{profile}:{message}")
            profiles[profile] = {
                "profile": profile_meta,
                "failure_kind": failure_kind,
                "script_error": message if failure_kind == "script_error" else None,
                "runtime_error": message if failure_kind == "runtime_error" else None,
                "model_sla_error": None,
                "verdict": "FAIL",
            }
            continue

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

        pilot_parallel = int(profile_meta.get("pilot_parallel") or "6")
        pilot_phase = f"parallel_{pilot_parallel}"
        sequential_ok = (
            phase_success["sequential"] == sequential_target
            and phase_error["sequential"] == 0
            and phase_stats.get("sequential", {}).get("p95_ms", 999999) <= 8000
        )
        pilot_ok = (
            phase_success[pilot_phase] == pilot_parallel
            and phase_error[pilot_phase] == 0
            and phase_stats.get(pilot_phase, {}).get("p95_ms", 999999) <= 8000
        )
        overload_total = phase_success["parallel_12"] + phase_error["parallel_12"]
        overload_errors = phase_error_taxonomy["parallel_12"]
        overload_all_success = overload_total == overload_target and phase_error["parallel_12"] == 0
        overload_controlled_queue = (
            allow_controlled_queue
            and overload_total == overload_target
            and phase_error["parallel_12"] > 0
            and set(overload_errors) <= {"codex_queue_timeout"}
        )
        queue_outside_overload = any(
            phase != "parallel_12" and "codex_queue_timeout" in errors
            for phase, errors in phase_error_taxonomy.items()
        )
        hard_errors = {"codex_turn_timeout", "codex_upstream_http_error", "codex_model_error", "codex_runtime_error"}
        hard_error_seen = any(error in error_taxonomy for error in hard_errors)
        safety_ok = (
            token_leakage_count == 0
            and dummy_token_success_count == 0
            and dummy_assistant_success_count == 0
            and invalid_json_count == 0
        )
        overload_ok = overload_all_success or overload_controlled_queue
        model_sla_error = None
        if not safety_ok:
            model_sla_error = "safety_or_output_gate_failed"
        elif not sequential_ok:
            model_sla_error = "sequential_sla_failed"
        elif not pilot_ok:
            model_sla_error = "pilot_parallel_sla_failed"
        elif not overload_ok:
            model_sla_error = "overload_backpressure_sla_failed"
        elif queue_outside_overload:
            model_sla_error = "queue_timeout_outside_overload_phase"
        elif hard_error_seen:
            model_sla_error = "hard_error_seen"

        verdict = "PASS" if model_sla_error is None and overload_all_success else "PASS_CONTROLLED_QUEUE" if model_sla_error is None else "FAIL"
        if model_sla_error:
            model_sla_errors.append(f"{profile}:{model_sla_error}")
        profiles[profile] = {
            **stats(all_elapsed),
            "profile": profile_meta,
            "pilot_phase": pilot_phase,
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
            "parallel_12_controlled_queue": overload_controlled_queue,
            "failure_kind": "model_sla_error" if model_sla_error else None,
            "script_error": None,
            "runtime_error": None,
            "model_sla_error": model_sla_error,
            "verdict": verdict,
        }

    passing = [
        (name, data)
        for name, data in profiles.items()
        if data.get("verdict") in {"PASS", "PASS_CONTROLLED_QUEUE"}
    ]
    recommended = None
    if passing:
        recommended = min(
            passing,
            key=lambda item: (
                -int(item[1].get("profile", {}).get("pilot_parallel") or "0"),
                item[1].get("phase_latency", {}).get(item[1].get("pilot_phase"), {}).get("p95_ms", 999999),
                item[1].get("error_count", 999999),
            ),
        )[0]

    if recommended:
        failure_kind = None
    elif script_errors:
        failure_kind = "script_error"
    elif runtime_errors and not model_sla_errors:
        failure_kind = "runtime_error"
    else:
        failure_kind = "model_sla_error"
    summary = {
        "profiles": profiles,
        "recommended_profile": recommended,
        "verdict": "PASS" if recommended else "FAIL",
        "failure_kind": failure_kind,
        "script_error": script_errors,
        "runtime_error": runtime_errors,
        "model_sla_error": model_sla_errors,
    }
    return summary, 0 if recommended else 1

try:
    summary, exit_code = main()
except Exception as exc:
    summary = {
        "profiles": {},
        "recommended_profile": None,
        "verdict": "FAIL",
        "failure_kind": "script_error",
        "script_error": [f"aggregation_failed:{exc.__class__.__name__}"],
        "runtime_error": [],
        "model_sla_error": [],
        "trace": traceback.format_exc(limit=1),
    }
    exit_code = 1
write_summary(summary)
sys.exit(exit_code)
PY
