# OpenClaw Codex Auth / Token / Conversation Vendor Reference

This repository vendors OpenClaw as an isolated Git submodule under `vendor/openclaw`.

Pinned upstream:

- Upstream repository: `https://github.com/openclaw/openclaw.git`
- Pinned commit: `8da8bc4aadfc7f62af864b24718896b538c069e3`
- License baseline: MIT, copyright OpenClaw Foundation.

## Purpose

This vendor reference preserves the upstream implementation surface for the Codex authorization chain:

1. OpenAI Codex OAuth login.
2. OpenAI Codex device-code login.
3. Authorization-code to access/refresh token exchange.
4. Access-token expiry and identity/profile resolution.
5. Auth profile persistence and profile ordering.
6. Refresh-token based OAuth renewal.
7. Codex provider registration, catalog, model/transport normalization, and usage-token resolution.
8. Codex app-server/auth bridge and thread/conversation lifecycle used for runtime dialogue operations.

## Boundary

`vendor/openclaw` is a pinned upstream reference, not a NexusDesk production runtime dependency.

Do not import from `vendor/openclaw` directly in Nexus backend or webapp code until a dedicated adapter RFC/PR maps the upstream concepts into Nexus-owned abstractions. NexusDesk should own logistics/customer-service workflow, permissions, audit logs, tenant isolation, ticket/customer/conversation linkage, and provider-token storage semantics.

## Materialize the submodule

From repository root:

```bash
git submodule update --init --recursive vendor/openclaw
```

After materializing the submodule, generate a flat extract of the Codex-related source files for review with:

```bash
bash scripts/vendor/export_openclaw_codex_auth_files.sh
```

The export script writes to:

```text
vendor/openclaw_codex_auth_reference/
```

That generated folder is intended for local audit / implementation planning. Do not commit generated copies unless a later PR explicitly decides to vendor a flat copy.

## Codex auth chain summary

### 1. Provider registration

Primary file:

```text
vendor/openclaw/extensions/openai/openai-codex-provider.ts
```

The OpenAI Codex provider registers auth methods for:

- standard OAuth login;
- device-code login;
- OpenAI API-key fallback.

It also wires provider catalog behavior, model transport normalization, OAuth refresh, usage-token resolution, and modern Codex model handling.

### 2. Device-code login to token

Primary file:

```text
vendor/openclaw/extensions/openai/openai-codex-device-code.ts
```

The device-code path performs:

1. request device auth user code;
2. show verification URL and user code;
3. poll device authorization endpoint;
4. exchange authorization code and code verifier for OAuth access/refresh tokens;
5. resolve token expiry.

### 3. Standard OAuth login to token

Primary files:

```text
vendor/openclaw/extensions/openai/openai-codex-oauth.runtime.ts
vendor/openclaw/src/plugins/provider-openai-codex-oauth.ts
vendor/openclaw/src/plugins/provider-openai-codex-oauth-tls.ts
```

These files implement the browser/OAuth runtime, callback exchange, provider OAuth handling, and local TLS/callback support.

### 4. Token identity, persistence, refresh

Primary files:

```text
vendor/openclaw/extensions/openai/openai-codex-auth-identity.ts
vendor/openclaw/src/agents/auth-profiles/oauth.ts
vendor/openclaw/src/agents/auth-profiles/persisted.ts
vendor/openclaw/src/agents/auth-profiles/external-cli-sync.ts
vendor/openclaw/src/agents/auth-profiles/usage.ts
```

This layer resolves identity/profile metadata, stores credentials as auth profiles, orders provider profiles, and resolves/refreshes OAuth credentials for later runtime use.

### 5. Dialogue/runtime operation

Primary files:

```text
vendor/openclaw/extensions/codex/src/app-server/auth-bridge.ts
vendor/openclaw/extensions/codex/src/app-server/thread-lifecycle.ts
vendor/openclaw/extensions/acpx/src/codex-auth-bridge.ts
vendor/openclaw/src/plugin-sdk/codex-native-task-runtime.ts
vendor/openclaw/src/agents/harness/codex-app-server-extensions.ts
vendor/openclaw/src/gateway/gateway-codex-harness.live-helpers.ts
```

This layer bridges resolved auth into the Codex runtime/app-server side and manages thread lifecycle / runtime dialogue operations.

## NexusDesk integration rule

Recommended next NexusDesk implementation is not to copy OpenClaw internals directly into production code. The production route should be:

```text
Nexus provider account table
  -> encrypted OAuth credential store
  -> token refresh service
  -> Codex provider adapter
  -> customer-service AI action policy
  -> ticket/conversation event audit
```

Keep OpenClaw as a reference source until a Nexus-owned adapter is implemented with tests, audit logging, tenant isolation, revocation, and rollback.

## Security rules

- Do not commit real access tokens, refresh tokens, cookies, OAuth callback payloads, CLI auth files, browser profiles, or local OpenClaw profile stores.
- Treat generated `vendor/openclaw_codex_auth_reference/` as source reference only.
- Any production token store must use encrypted at-rest storage and explicit operator/admin revocation.
- Any use for customer-service automation must write tool-call, model-call, outbound-message, and human-override events to Nexus audit history.
