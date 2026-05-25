# webapp/AGENTS.md — Operator Console Execution Contract

This contract applies to `webapp/**`. `webapp/` is the modern operator console source of truth. `frontend_dist/` is generated build output and must not be edited directly.

## 1. Current stack

```text
React 18.3.1
TypeScript
Vite
TanStack Router
TanStack Query
Tailwind CSS v4
Radix UI primitives
Playwright
```

Entry and core contracts:

```text
webapp/src/main.tsx             React root, QueryClientProvider, RouterProvider, web vitals
webapp/src/router.tsx           route tree
webapp/src/lib/api.ts           backend API client, auth/session handling, request IDs, timeout/retry behavior
webapp/src/lib/types.ts         typed API models
webapp/src/routes/**            page routes
webapp/src/components/**        reusable UI and domain components
webapp/tests/**                 Node tests / contract tests
```

## 2. Mandatory inspection before UI changes

Before editing a route/component, inspect:

```text
webapp/src/router.tsx
webapp/src/lib/api.ts
webapp/src/lib/types.ts
relevant webapp/src/routes/<route>.tsx
relevant webapp/src/components/**
matching backend route under backend/app/api/**
matching backend schema/service/model when response shape changes
webapp/tests/** if behavior is covered
```

Do not implement UI against guessed endpoints. Extend `webapp/src/lib/api.ts` and `webapp/src/lib/types.ts` when a real backend contract exists.

## 3. Route map

Current route tree includes:

```text
/login
/admin
/
/workspace
/webchat
/webchat-voice
/webcall
/webcall-ai
/webcall-ai-demo
/provider-credentials
/bulletins
/ai-control
/control-plane
/accounts
/users
/runtime
```

Rules:

- Keep internal routes internal if current comments/usage mark them as operator-only or ops-only.
- Do not expose demo/sandbox route behavior as production customer flow without backend feature flag and tests.
- Do not add primary navigation entries for internal-only routes unless the task explicitly asks for it.

## 4. API client rules

`webapp/src/lib/api.ts` owns API base normalization, request ID header, auth token handling, timeout, retry, and error mapping.

Do not regress:

```text
normalizeApiBaseUrl()
buildApiUrl()
PUBLIC_API_PATHS
SAFE_RETRY_METHODS
REQUEST_ID_HEADER = X-Request-Id
getToken()/setToken()/clearToken()
sessionStorage token custody
AuthExpiredError behavior
ApiError behavior
fetchWithTimeout()
frontend latency event: nexusdesk:api-latency
```

Hard stops:

```text
Do not move auth token storage from sessionStorage to localStorage.
Do not add Authorization header to public endpoints.
Do not retry unsafe write methods by default.
Do not remove request ID propagation.
Do not silently swallow 401; preserve login-expired behavior.
Do not introduce `/api/api/...` URL duplication.
```

## 5. Feature-specific UI contracts

### Case workspace / tickets

API client methods include:

```text
casesPage()
cases()
caseDetail()
ticketTimeline()
ticketOutboundChannelCapabilities()
sendOutboundMessage()
workflowUpdate()
aiIntake()
```

When modifying case UI, preserve:

```text
pagination/cursor support
status/priority/team/assignee filters
outbound capability checks before send
human-readable loading/error/empty states
no direct customer send without backend capability response
```

### Bulletins / AI control / knowledge / persona

API client methods include:

```text
markets()
bulletins()
createBulletin()
updateBulletin()
aiConfigs()
publishedAIConfigs()
createAIConfig()
updateAIConfig()
publishAIConfig()
aiConfigVersions()
rollbackAIConfig()
personaProfiles()
knowledgeItems()
```

Preserve publish/rollback semantics. Do not show draft content as published customer-facing context unless backend marks it published.

### Channel accounts / runtime / OpenClaw

API client methods include:

```text
channelAccounts()
createChannelAccount()
updateChannelAccount()
runtimeHealth()
openclawConnectivityCheck()
consumeOpenClawEventsOnce()
unresolvedEvents()
replayUnresolvedEvent()
dropUnresolvedEvent()
```

Hard stops:

```text
No hidden destructive action.
No replay/drop action without visible confirmation or explicit operator intent.
No runtime health page that hides failed/degraded daemon states.
```

### Provider credentials / Codex

API client methods include:

```text
codexCredentialStatus()
startCodexAuthorization()
startCodexManualAuthorization()
completeCodexManualAuthorization()
startCodexDeviceFlow()
codexDeviceFlowStatus()
pollCodexDeviceFlow()
refreshCodexCredential()
revokeCodexCredential()
disconnectCodexCredential()
```

Hard stops:

```text
Do not render token values.
Do not log authorization responses.
Do not persist authorization response in browser storage.
Do not present smoke chat as a production customer chat flow.
Credential mutation controls must be admin/runtime-management gated by backend.
```

### WebChat operator

API client methods include:

```text
webchatConversations()
webchatThread()
webchatEvents()
webchatReply()
```

Preserve:

```text
thread/event polling safety
fact-evidence confirmation fields
operator review before customer reply
clear error states for failed send/reply
```

### WebCall / voice

API client methods include:

```text
webchatVoiceRuntimeConfig()
webchatVoiceIncomingSessions()
webchatVoiceSessions()
webchatVoiceAcceptSession()
webchatVoiceRejectSession()
webchatVoiceEndSession()
webcallAIDemoStatus()
webcallAIDemoCreateSession()
webcallAIDemoTurn()
webcallAIDemoEndSession()
webcallAIDemoEvents()
```

Hard stops:

```text
Do not acquire microphone without visible user/operator action.
Do not leave acquired tracks active on reject/end/non-LiveKit fallback.
Do not expose demo-only WebCall AI as real customer production without feature flag and tests.
Do not hide provider/livekit failure; show actionable degraded state.
```

## 6. UX production bar

Every operator-facing workflow must include:

```text
loading state
empty state
error state
permission/disabled state when action is unavailable
success confirmation for write actions
clear destructive-action affordance
keyboard-reachable controls
labels for non-icon-only actions
```

No fake buttons. A control must either:

```text
call a real API;
be disabled with a reason;
be hidden until supported;
or be explicitly marked demo/internal.
```

## 7. Styling rule

Use existing design tokens/classes and component patterns. Do not introduce a second visual system. Avoid inline one-off styling unless localized and justified.

## 8. Required validation

For any webapp code change:

```bash
set -Eeuo pipefail
cd webapp
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run size-report
```

For route/page behavior changes:

```bash
npm run e2e
```

If Playwright browser dependencies are unavailable locally, state the exact blocker and rely on CI, but still run lint/typecheck/test/build.

## 9. Cross-layer rule

If the UI requires a backend capability that does not exist, do not mock it as if it is real. Either:

```text
implement backend route/service/test first;
mark UI as disabled with explicit reason;
or document the missing backend contract in PR risk.
```
