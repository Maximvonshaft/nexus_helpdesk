# tools/nexus-codex-runtime/AGENTS.md — Codex Runtime Sidecar Execution Contract

This contract applies to `tools/nexus-codex-runtime/**`. This Node/TypeScript package is the Codex app-server runtime sidecar. It must remain a controlled reply provider behind NexusDesk policy gates.

## 1. Runtime contract

Current server surface:

```text
GET  /healthz
GET  /readyz
POST /reply
```

Core implementation anchors:

```text
src/server.ts              HTTP server, route dispatch, semaphore, request body limit, headers, redaction
src/env.ts                 runtime config and env loading
src/client-cache.ts        Codex appserver client cache and login state
src/account-login.ts       account login flow
src/deadline.ts            request deadline handling
src/metrics.ts             stage timing
src/reply-contract.ts      request validation and strict reply parsing
src/redaction.ts           response/log redaction
src/thread-runner.ts       ephemeral thread execution
test/*.test.ts             runtime contract tests
```

## 2. Authority boundary

The sidecar may return a structured reply to NexusDesk. It must not directly:

```text
modify NexusDesk tickets;
send customer outbound messages;
execute shell commands;
write repository files;
read cookies/browser sessions;
run model-native tools;
perform Speedaf actions;
refund, compensate, claim, cancel, change address, or dispatch;
expose tokens or raw upstream payloads.
```

NexusDesk remains the control plane, parser, policy gate, audit boundary, and final customer-action authority.

## 3. `/reply` hard requirements

Preserve:

```text
config.enabled fail-closed check
request body size limit, currently 128 KiB
validateReplyRequest()
clientCacheKey() without access-token churn as a cache key
cache.getOrCreate()
cache.ensureLoggedIn()
loginFingerprint()
runEphemeralThread()
parseStrictReply()
redact() before response
StageTimer stage_ms
Semaphore maxConcurrency and queue timeout
X-Nexus-Codex-* diagnostic headers
no-store JSON responses
```

Do not weaken strict reply parsing. Invalid upstream assistant output must not pass through as customer reply.

## 4. Required response headers

`POST /reply` should preserve diagnostic headers unless deliberately changed with tests:

```text
X-Nexus-Codex-Backend
X-Nexus-Codex-Elapsed-Ms
X-Nexus-Codex-Client-Cache
X-Nexus-Codex-Login
X-Nexus-Codex-Thread-Mode
X-Nexus-Codex-Upstream-SHA
```

Do not add headers that expose secrets, account tokens, raw prompts, or raw upstream response text.

## 5. Timeout and concurrency rules

Do not remove:

```text
request deadline handling
queue timeout
reply timeout
stage timings
semaphore release in finally
```

Any new async path must release acquired resources in `finally` and must return normalized errors.

## 6. Error behavior

Errors must normalize to safe codes. Preserve:

```text
RuntimeError
normalizeError()
error_stage where applicable
safe status codes
redacted payloads
```

Do not leak stack traces, tokens, raw prompts, raw assistant text, or account identifiers beyond already-approved redacted diagnostics.

## 7. Test requirements

For any change in this package:

```bash
set -Eeuo pipefail
cd tools/nexus-codex-runtime
npm ci
npm run build
npm test
```

If the change affects Nexus provider runtime integration, also run targeted backend tests for provider runtime and WebChat Codex provider behavior.

## 8. Integration with backend

When changing `/reply` request or response contract, update all relevant backend files and tests:

```text
backend/app/services/provider_runtime/**
backend/app/services/ai_runtime/**
backend/tests/test_webchat_codex_app_server_provider.py
backend/tests/test_webchat_fast_reply_provider_runtime.py
backend/tests/test_provider_runtime_router_fallback_e2e.py
```

Do not change the sidecar contract alone and leave backend parser/gate behavior stale.
