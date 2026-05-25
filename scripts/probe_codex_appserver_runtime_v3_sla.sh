#!/usr/bin/env bash
set -Eeuo pipefail

BRIDGE_URL="${CODEX_APP_SERVER_BRIDGE_URL:-http://127.0.0.1:18794/reply}"
TOKEN_FILE="${CODEX_APP_SERVER_TOKEN_FILE:-/run/nexus/codex_app_server_bridge_token}"
ORIGIN="${CODEX_APP_SERVER_PROBE_ORIGIN:-https://nexusdesk.local}"
OUT_DIR="${OUT_DIR:-/tmp/nexus_codex_runtime_v3_sla_$(date -u '+%Y%m%dT%H%M%SZ')}"
SEQUENTIAL="${CODEX_APPSERVER_SLA_SEQUENTIAL:-20}"
PARALLEL_6="${CODEX_APPSERVER_SLA_PARALLEL_6:-6}"
PARALLEL_12="${CODEX_APPSERVER_SLA_PARALLEL_12:-12}"
ALLOW_CONTROLLED_QUEUE_TIMEOUT="${CODEX_APPSERVER_SLA_ALLOW_CONTROLLED_QUEUE_TIMEOUT:-true}"
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

payload() {
  local token="$1"
  local session="$2"
  cat <<JSON
{"login":{"type":"chatgptAuthTokens","accessToken":"$token","chatgptAccountId":"sla-account","chatgptPlanType":"plus"},"body":"Hello, please reply with strict JSON.","messages":[],"contract":"speedaf_webchat_fast_reply_v1","tracking_fact_summary":null,"tracking_fact_evidence_present":false,"tenant_id":"default","channel_key":"website","session_id":"$session"}
JSON
}

run_one() {
  local phase="$1"
  local idx="$2"
  local token="$3"
  local id="${phase}_${idx}"
  local body="$OUT_DIR/response_${id}.json"
  local headers="$OUT_DIR/headers_${id}.txt"
  local meta="$OUT_DIR/meta_${id}.txt"
  local start end code elapsed
  start="$(date +%s%3N)"
  code="$(payload "$token" "sla-${phase}-${idx}" | curl -sS -D "$headers" -o "$body" -w '%{http_code}' \
    -H "Authorization: Bearer $BRIDGE_TOKEN" \
    -H "Content-Type: application/json" \
    -H "Origin: $ORIGIN" \
    --data-binary @- \
    "$BRIDGE_URL" || true)"
  end="$(date +%s%3N)"
  elapsed=$((end-start))
  {
    printf 'phase=%s\n' "$phase"
    printf 'idx=%s\n' "$idx"
    printf 'code=%s\n' "$code"
    printf 'elapsed_ms=%s\n' "$elapsed"
  } > "$meta"
}

for i in $(seq 1 "$SEQUENTIAL"); do
  run_one "sequential" "$i" "$VALID_TOKEN"
done

for i in $(seq 1 "$PARALLEL_6"); do
  run_one "parallel_6" "$i" "$VALID_TOKEN" &
done
wait

for i in $(seq 1 "$PARALLEL_12"); do
  run_one "parallel_12" "$i" "$VALID_TOKEN" &
done
wait

run_one "dummy_negative" "1" "dummy-token"

python - "$OUT_DIR" "$VALID_TOKEN" "$ALLOW_CONTROLLED_QUEUE_TIMEOUT" <<'PY'
import json
import math
import os
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

phase_elapsed: dict[str, list[int]] = defaultdict(list)
phase_success: Counter[str] = Counter()
phase_error: Counter[str] = Counter()
error_taxonomy: Counter[str] = Counter()
phase_error_taxonomy: dict[str, Counter[str]] = defaultdict(Counter)
backend_seen: set[str] = set()
reply_source_seen: set[str] = set()
token_leakage_count = 0
dummy_token_success_count = 0
dummy_assistant_success_count = 0
invalid_json_count = 0

def parse_meta(path: Path) -> dict[str, str]:
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

for meta_path in sorted(out.glob("meta_*.txt")):
    meta = parse_meta(meta_path)
    phase = meta.get("phase", "unknown")
    idx = meta.get("idx", "0")
    code = int(meta.get("code", "0") or "0")
    elapsed = int(meta.get("elapsed_ms", "0") or "0")
    body_path = out / f"response_{phase}_{idx}.json"
    headers_path = out / f"headers_{phase}_{idx}.txt"
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
            else:
                err = f"http_{code}"
            error_taxonomy[err] += 1
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
)
verdict = "PASS" if not hard_fail and parallel_12_all_success else "PASS_CONTROLLED_QUEUE" if not hard_fail else "FAIL"

summary = {
    **stats(all_elapsed),
    "success_count": sum(phase_success.values()),
    "error_count": sum(phase_error.values()),
    "timeout_count": sum(count for err, count in error_taxonomy.items() if "timeout" in err),
    "invalid_json_count": invalid_json_count,
    "token_leakage_count": token_leakage_count,
    "dummy_token_success_count": dummy_token_success_count,
    "dummy_assistant_success_count": dummy_assistant_success_count,
    "error_taxonomy_summary": dict(sorted(error_taxonomy.items())),
    "phase_counts": phase_counts,
    "phase_latency": phase_stats,
    "backend_seen": sorted(backend_seen),
    "reply_source_seen": sorted(reply_source_seen),
    "parallel_12_controlled_queue": parallel_12_controlled_queue,
    "verdict": verdict,
}
print(json.dumps(summary, indent=2, sort_keys=True))
(out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
sys.exit(1 if hard_fail else 0)
PY
