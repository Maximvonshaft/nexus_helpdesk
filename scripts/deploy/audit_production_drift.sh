#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-https://www.leakle.com}"
OUT_DIR="${OUT_DIR:-forensics/production_drift_$(date -u +%Y%m%dT%H%M%SZ)}"
EXPECTED_GIT_SHA="${EXPECTED_GIT_SHA:-}"
EXPECTED_IMAGE_TAG="${EXPECTED_IMAGE_TAG:-}"
CANONICAL_SOURCE_DIR="${CANONICAL_SOURCE_DIR:-/opt/nexus_helpdesk}"
CANDIDATE_ROOT="${CANDIDATE_ROOT:-/opt/nexus_candidate}"
NGINX_SITE="${NGINX_SITE:-/etc/nginx/sites-enabled/nexusdesk}"

mkdir -p "$OUT_DIR"

curl_json() {
  local url="$1"
  local out="$2"
  curl -fsS --max-time 12 -H 'Accept: application/json' "$url" -o "$out"
}

git_snapshot() {
  local dir="$1"
  local name="$2"
  local out="$OUT_DIR/${name}.txt"
  {
    printf 'path=%s\n' "$dir"
    if [ ! -d "$dir/.git" ]; then
      printf 'git_present=false\n'
      return 0
    fi
    printf 'git_present=true\n'
    printf 'head=%s\n' "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
    printf 'branch=%s\n' "$(git -C "$dir" branch --show-current 2>/dev/null || true)"
    printf 'left_right_head_origin_main=%s\n' "$(git -C "$dir" rev-list --left-right --count HEAD...origin/main 2>/dev/null || true)"
    printf 'dirty_count=%s\n' "$(git -C "$dir" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
    printf 'status_short_branch=\n'
    git -C "$dir" status --short --branch 2>/dev/null || true
    printf 'status_porcelain_first_40=\n'
    git -C "$dir" status --porcelain 2>/dev/null | sed -n '1,40p' || true
  } > "$out"
}

echo "== public health =="
curl_json "${BASE_URL%/}/healthz" "$OUT_DIR/healthz.json"
curl_json "${BASE_URL%/}/readyz" "$OUT_DIR/readyz.json"

echo "== docker containers =="
if command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' \
    | grep -E '(nexusdesk|deploy-app|candidate|1808[0-9])' \
    > "$OUT_DIR/docker_ps_filtered.txt" || true
else
  printf 'docker_unavailable\n' > "$OUT_DIR/docker_ps_filtered.txt"
fi

echo "== nginx upstream ports =="
if [ -r "$NGINX_SITE" ]; then
  grep -Eo '127\.0\.0\.1:[0-9]+' "$NGINX_SITE" \
    | sort \
    | uniq -c \
    > "$OUT_DIR/nginx_loopback_upstreams.txt" || true
else
  printf 'nginx_site_unreadable=%s\n' "$NGINX_SITE" > "$OUT_DIR/nginx_loopback_upstreams.txt"
fi

echo "== git snapshots =="
git_snapshot "$CANONICAL_SOURCE_DIR" canonical_source
if [ -d "$CANDIDATE_ROOT" ]; then
  find "$CANDIDATE_ROOT" -maxdepth 2 -type d -name .git \
    | sed 's#/.git$##' \
    | sort \
    > "$OUT_DIR/candidate_source_dirs.txt"
  while IFS= read -r candidate_dir; do
    safe_name="$(printf '%s' "$candidate_dir" | sed 's#[/:]#_#g; s#^_*##')"
    git_snapshot "$candidate_dir" "candidate_${safe_name}"
  done < "$OUT_DIR/candidate_source_dirs.txt"
else
  printf 'candidate_root_absent=%s\n' "$CANDIDATE_ROOT" > "$OUT_DIR/candidate_source_dirs.txt"
fi

python3 - "$OUT_DIR" "$EXPECTED_GIT_SHA" "$EXPECTED_IMAGE_TAG" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
expected_git_sha = sys.argv[2]
expected_image_tag = sys.argv[3]

healthz = json.loads((out_dir / "healthz.json").read_text(encoding="utf-8"))
readyz = json.loads((out_dir / "readyz.json").read_text(encoding="utf-8"))
nginx_ports = (out_dir / "nginx_loopback_upstreams.txt").read_text(encoding="utf-8", errors="replace").splitlines()
docker_rows = (out_dir / "docker_ps_filtered.txt").read_text(encoding="utf-8", errors="replace").splitlines()
canonical = (out_dir / "canonical_source.txt").read_text(encoding="utf-8", errors="replace")


def field(text: str, key: str) -> str:
    prefix = key + "="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return ""


canonical_dirty = field(canonical, "dirty_count")
canonical_head = field(canonical, "head")
canonical_left_right = field(canonical, "left_right_head_origin_main")

errors: list[str] = []
if healthz.get("status") != "ok":
    errors.append(f"healthz_status={healthz.get('status')}")
if readyz.get("status") != "ready":
    errors.append(f"readyz_status={readyz.get('status')}")
if readyz.get("database") != "ok":
    errors.append(f"readyz_database={readyz.get('database')}")
if expected_git_sha and healthz.get("git_sha") != expected_git_sha:
    errors.append(f"public_git_sha={healthz.get('git_sha')} expected={expected_git_sha}")
if expected_image_tag and healthz.get("image_tag") != expected_image_tag:
    errors.append(f"public_image_tag={healthz.get('image_tag')} expected={expected_image_tag}")

summary = {
    "public": {
        "base_health_status": healthz.get("status"),
        "ready_status": readyz.get("status"),
        "database": readyz.get("database"),
        "git_sha": healthz.get("git_sha"),
        "image_tag": healthz.get("image_tag"),
        "app_version": healthz.get("app_version"),
        "release_metadata_complete": healthz.get("release_metadata_complete"),
    },
    "canonical_source": {
        "head": canonical_head,
        "dirty_count": canonical_dirty,
        "left_right_head_origin_main": canonical_left_right,
        "matches_public_git_sha": bool(canonical_head and canonical_head == healthz.get("git_sha")),
    },
    "runtime_inventory": {
        "nginx_loopback_upstreams": nginx_ports,
        "docker_rows": docker_rows,
    },
    "classification": {
        "keep": [
            "public image metadata when it matches GitHub release metadata",
            "candidate rollback container until the next stable release is proven",
        ],
        "rewrite": [
            "dirty canonical source tree into a clean release checkout plus env-only local state",
            "nginx and compose runtime values through templates instead of host edits",
        ],
        "drop": [
            "unused old candidate containers after rollback window expires",
            "manual container mutations that are not represented by GitHub-built images",
        ],
    },
    "errors": errors,
}
(out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(summary, indent=2, ensure_ascii=False))
if errors:
    raise SystemExit("\n".join(errors))
PY

echo "PRODUCTION_DRIFT_AUDIT_PASS=true"
echo "evidence_dir=$OUT_DIR"
