# OpenClaw Codex Auth Related File Manifest

Pinned OpenClaw commit: `8da8bc4aadfc7f62af864b24718896b538c069e3`

This manifest enumerates the upstream files that are relevant to how OpenClaw turns Codex authorization into usable tokens and dialogue/runtime operations.

## Core OpenAI Codex auth / provider files

| Area | Upstream path under `vendor/openclaw` | Why it matters |
| --- | --- | --- |
| Provider registration | `extensions/openai/openai-codex-provider.ts` | Registers `openai-codex`, auth choices, catalog, transport normalization, OAuth refresh, usage token resolution. |
| Browser OAuth runtime | `extensions/openai/openai-codex-oauth.runtime.ts` | Implements Codex OAuth runtime behavior. |
| Device-code OAuth | `extensions/openai/openai-codex-device-code.ts` | Requests user/device code, polls authorization, exchanges authorization code for access/refresh tokens. |
| Token identity | `extensions/openai/openai-codex-auth-identity.ts` | Resolves identity/profile metadata and access-token expiry. |
| OAuth provider bridge | `src/plugins/provider-openai-codex-oauth.ts` | Shared/provider SDK OAuth bridge for Codex. |
| OAuth TLS helper | `src/plugins/provider-openai-codex-oauth-tls.ts` | Local TLS/callback support for OAuth handling. |
| Usage fetch | `src/infra/provider-usage.fetch.codex.ts` | Fetches Codex provider usage using resolved token/account context. |
| Provider catalog | `extensions/codex/provider-catalog.ts` | Codex provider catalog metadata. |
| Provider discovery | `extensions/codex/provider-discovery.ts` | Runtime provider discovery. |
| Codex provider wrapper | `extensions/codex/provider.ts` | Codex extension/provider entry behavior. |
| Codex extension entry | `extensions/codex/index.ts` | Codex extension entrypoint. |
| Codex manifest | `extensions/codex/openclaw.plugin.json` | Plugin manifest and runtime metadata. |

## Auth profile / token persistence files

| Area | Upstream path under `vendor/openclaw` | Why it matters |
| --- | --- | --- |
| OAuth profile logic | `src/agents/auth-profiles/oauth.ts` | OAuth profile resolution and credential handling. |
| Persisted profiles | `src/agents/auth-profiles/persisted.ts` | Auth profile store persistence. |
| Profile constants | `src/agents/auth-profiles/constants.ts` | Constants used by profile storage/order. |
| Profile ordering | `src/agents/auth-profiles/order.ts` | Auth profile ordering and selection. |
| External CLI sync | `src/agents/auth-profiles/external-cli-sync.ts` | Sync behavior with external CLI credentials. |
| Profile usage | `src/agents/auth-profiles/usage.ts` | Usage tracking over auth profiles. |
| Legacy sidecar | `src/agents/auth-profiles/legacy-oauth-sidecar.ts` | Compatibility path for old OAuth sidecar profiles. |
| CLI credentials | `src/agents/cli-credentials.ts` | CLI credential discovery and normalization. |
| Model auth command | `src/commands/models/auth.ts` | CLI command entry for provider model auth. |
| Doctor auth | `src/commands/doctor-auth.ts` | Auth diagnostics and migration checks. |
| Codex account command | `extensions/codex/src/command-account.ts` | Codex account/auth command surface. |

## Codex runtime / dialogue operation files

| Area | Upstream path under `vendor/openclaw` | Why it matters |
| --- | --- | --- |
| App-server auth bridge | `extensions/codex/src/app-server/auth-bridge.ts` | Bridges resolved auth into Codex app-server runtime. |
| Thread lifecycle | `extensions/codex/src/app-server/thread-lifecycle.ts` | Manages Codex runtime thread lifecycle. |
| ACPX auth bridge | `extensions/acpx/src/codex-auth-bridge.ts` | Auth bridge used by ACPX/Codex runtime. |
| Trust config | `extensions/acpx/src/codex-trust-config.ts` | Trust/config constraints for Codex runtime. |
| Native task runtime | `src/plugin-sdk/codex-native-task-runtime.ts` | Native Codex task/runtime execution surface. |
| MCP projection | `src/plugin-sdk/codex-mcp-projection.ts` | Projects plugin/MCP capabilities for Codex. |
| Codex MCP config | `src/agents/codex-mcp-config.ts` | Codex MCP configuration. |
| Codex routing | `src/agents/openai-codex-routing.ts` | Codex routing behavior. |
| Bundle MCP Codex | `src/agents/cli-runner/bundle-mcp-codex.ts` | Bundles MCP/Codex runtime for CLI runner. |
| App-server extensions | `src/agents/harness/codex-app-server-extensions.ts` | Harness-side Codex app-server extension wiring. |
| Gateway helpers | `src/gateway/gateway-codex-harness.live-helpers.ts` | Live gateway/harness helpers for Codex runtime. |
| Commands | `extensions/codex/src/commands.ts` | Codex extension command surface. |
| Media provider | `extensions/codex/media-understanding-provider.ts` | Codex media understanding provider behavior. |

## Docs and QA references

| Area | Upstream path under `vendor/openclaw` |
| --- | --- |
| Codex reference | `docs/plugins/reference/codex.md` |
| Codex harness | `docs/plugins/codex-harness.md` |
| Codex harness runtime | `docs/plugins/codex-harness-runtime.md` |
| Native Codex plugins | `docs/plugins/codex-native-plugins.md` |
| Codex computer use | `docs/plugins/codex-computer-use.md` |
| OAuth concept | `docs/concepts/oauth.md` |
| Gateway authentication | `docs/gateway/authentication.md` |
| Model providers | `docs/concepts/model-providers.md` |
| Runtime cold install QA | `qa/scenarios/runtime/codex-plugin-cold-install.md` |
| Runtime pinned new QA | `qa/scenarios/runtime/codex-plugin-pinned-new.md` |
| Runtime install race QA | `qa/scenarios/runtime/codex-plugin-install-race.md` |
| Mixed auth profile QA | `qa/scenarios/runtime/auth-profile-codex-mixed-profiles.md` |

## Extraction policy

The repository keeps `vendor/openclaw` as the canonical pinned source. Use `scripts/vendor/export_openclaw_codex_auth_files.sh` to generate a flat local reference folder for review.

Do not commit generated flat copies until the integration approach is approved, because direct flat-vendoring makes upstream diff/upgrade/license review harder.
