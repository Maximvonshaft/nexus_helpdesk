#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/vendor/openclaw"
DST="$ROOT/vendor/openclaw_codex_auth_reference"
PINNED_OPENCLAW_COMMIT="8da8bc4aadfc7f62af864b24718896b538c069e3"

if [[ ! -d "$SRC" ]]; then
  echo "ERROR: vendor/openclaw does not exist." >&2
  echo "Run: git submodule update --init --recursive vendor/openclaw" >&2
  exit 1
fi

if [[ ! -d "$SRC/.git" && ! -f "$SRC/.git" ]]; then
  echo "ERROR: vendor/openclaw is not initialized as a git submodule." >&2
  echo "Run: git submodule update --init --recursive vendor/openclaw" >&2
  exit 1
fi

if command -v git >/dev/null 2>&1; then
  actual_commit="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "$actual_commit" && "$actual_commit" != "$PINNED_OPENCLAW_COMMIT" ]]; then
    echo "WARN: vendor/openclaw is at $actual_commit, expected $PINNED_OPENCLAW_COMMIT" >&2
  fi
fi

rm -rf "$DST"
mkdir -p "$DST"

files=(
  "extensions/openai/openai-codex-provider.ts"
  "extensions/openai/openai-codex-oauth.runtime.ts"
  "extensions/openai/openai-codex-device-code.ts"
  "extensions/openai/openai-codex-auth-identity.ts"
  "src/plugins/provider-openai-codex-oauth.ts"
  "src/plugins/provider-openai-codex-oauth-tls.ts"
  "src/infra/provider-usage.fetch.codex.ts"
  "extensions/codex/index.ts"
  "extensions/codex/provider.ts"
  "extensions/codex/provider-catalog.ts"
  "extensions/codex/provider-discovery.ts"
  "extensions/codex/openclaw.plugin.json"
  "extensions/codex/src/commands.ts"
  "extensions/codex/src/command-account.ts"
  "extensions/codex/src/app-server/auth-bridge.ts"
  "extensions/codex/src/app-server/thread-lifecycle.ts"
  "extensions/codex/src/migration/auth.ts"
  "extensions/acpx/src/codex-auth-bridge.ts"
  "extensions/acpx/src/codex-trust-config.ts"
  "src/agents/auth-profiles/oauth.ts"
  "src/agents/auth-profiles/persisted.ts"
  "src/agents/auth-profiles/constants.ts"
  "src/agents/auth-profiles/order.ts"
  "src/agents/auth-profiles/external-cli-sync.ts"
  "src/agents/auth-profiles/usage.ts"
  "src/agents/auth-profiles/legacy-oauth-sidecar.ts"
  "src/agents/cli-credentials.ts"
  "src/commands/models/auth.ts"
  "src/commands/doctor-auth.ts"
  "src/plugin-sdk/codex-native-task-runtime.ts"
  "src/plugin-sdk/codex-mcp-projection.ts"
  "src/agents/codex-mcp-config.ts"
  "src/agents/openai-codex-routing.ts"
  "src/agents/cli-runner/bundle-mcp-codex.ts"
  "src/agents/harness/codex-app-server-extensions.ts"
  "src/gateway/gateway-codex-harness.live-helpers.ts"
  "extensions/codex/media-understanding-provider.ts"
  "docs/plugins/reference/codex.md"
  "docs/plugins/codex-harness.md"
  "docs/plugins/codex-harness-runtime.md"
  "docs/plugins/codex-native-plugins.md"
  "docs/plugins/codex-computer-use.md"
  "docs/concepts/oauth.md"
  "docs/gateway/authentication.md"
  "docs/concepts/model-providers.md"
  "qa/scenarios/runtime/codex-plugin-cold-install.md"
  "qa/scenarios/runtime/codex-plugin-pinned-new.md"
  "qa/scenarios/runtime/codex-plugin-install-race.md"
  "qa/scenarios/runtime/auth-profile-codex-mixed-profiles.md"
)

copied=0
missing=0
manifest="$DST/EXTRACT_MANIFEST.tsv"
printf "status\tpath\n" > "$manifest"

for rel in "${files[@]}"; do
  src_path="$SRC/$rel"
  dst_path="$DST/$rel"
  if [[ -f "$src_path" ]]; then
    mkdir -p "$(dirname "$dst_path")"
    cp "$src_path" "$dst_path"
    printf "copied\t%s\n" "$rel" >> "$manifest"
    copied=$((copied + 1))
  else
    printf "missing\t%s\n" "$rel" >> "$manifest"
    echo "WARN missing: $rel" >&2
    missing=$((missing + 1))
  fi
done

cat > "$DST/README.md" <<EOF
# OpenClaw Codex Auth Reference Extract

Generated from pinned upstream OpenClaw commit:

\`$PINNED_OPENCLAW_COMMIT\`

This folder is a local generated source extract for NexusDesk audit and adapter design. It is not intended to be committed by default.

Generated files copied: $copied
Missing file entries: $missing

See:

- \`docs/vendor/OPENCLAW_CODEX_AUTH_VENDOR.md\`
- \`docs/vendor/OPENCLAW_CODEX_AUTH_MANIFEST.md\`
EOF

echo "Export complete. copied=$copied missing=$missing dst=$DST"
