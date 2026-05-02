# 08 — Security Threat Model

## Status

Proposed. This document must be reviewed before implementation starts.

## Scope

This threat model covers the frontend upgrade, WebChat runtime, AI Governance Studio, authenticated admin console, and realtime event layer.

## Security boundaries

```text
Public visitor browser
  → public WebChat API only

Authenticated admin user
  → authenticated admin API

OpenClaw / Bridge / MCP
  → integration API and governed internal services

AI runtime
  → governed by published configs and safety gate
```

## Assets to protect

- admin auth token
- visitor token
- internal ticket ids
- customer messages
- attachments and POD evidence
- AI policy and system instructions
- OpenClaw integration status and event payloads
- channel account configuration
- runtime logs
- environment secrets

## Public WebChat threats

### Hostile website embedding widget

Risk:

- untrusted sites embed the widget to spam public APIs or impersonate a customer channel.

Mitigation:

- persisted origin allowlist per tenant/channel
- server-side origin validation
- rate limiting
- tenant/channel config validation
- no sensitive data returned to public origin

### Visitor token theft

Risk:

- browser-local visitor token is leaked through XSS on host website.

Mitigation:

- visitor token is scoped only to one conversation
- token cannot access admin APIs
- token cannot expose internal ticket ids
- token rotation strategy in later phase
- avoid logging token in console or error reports

### Internal id exposure

Risk:

- visitor learns internal numeric ticket id or internal workflow state.

Mitigation:

- public API returns only conversation-safe identifiers
- admin APIs remain separate
- response schemas reviewed for public exposure

### Message spam / oversized messages

Risk:

- public API abuse causes storage and operational noise.

Mitigation:

- server-side rate limiting
- message length limits
- client-side limits as usability aid only
- abuse telemetry in runtime control tower

## Admin console threats

### Token leakage

Risk:

- admin token leaks through logs, local storage, errors, or screenshots.

Mitigation:

- keep centralized auth token handling
- do not log Authorization headers
- avoid exposing token in UI
- consider moving from sessionStorage to more secure cookie strategy in future auth hardening

### Unauthorized admin actions

Risk:

- user accesses AI config, users, channel accounts, or runtime actions without permission.

Mitigation:

- server-side authorization remains source of truth
- frontend hides unavailable actions only as UX aid
- every dangerous mutation must be server-permission checked

### Unsafe mutation by stale UI

Risk:

- user submits actions based on stale ticket or policy state.

Mitigation:

- show stale indicators
- protect dirty forms from polling overwrite
- include updated timestamps or version checks for high-risk updates where needed

## AI threats

### Prompt injection

Risk:

- customer message tries to override system policy or reveal hidden instructions.

Mitigation:

- published policy guardrails have priority over customer content
- AI suggestions are treated as suggestions, not verified facts
- safety gate reviews outbound text
- operator sees policy/safety decision when relevant

### Sensitive data exposure

Risk:

- AI reply includes secrets, tokens, stack traces, internal implementation details, or private notes.

Mitigation:

- safety gate blocks sensitive terms and patterns
- never include secrets in AI context
- separate internal notes from customer-visible replies
- redact sensitive values in logs and telemetry

### Unsupported logistics factual commitment

Risk:

- AI promises delivery, refund, compensation, location, or timeline without evidence.

Mitigation:

- evidence-aware reply generation
- explicit `needs_review` or `unsupported_fact` state
- human confirmation flag for factual commitments
- UI shows evidence requirement before send

## Realtime event threats

### Cross-tenant or cross-user event leakage

Risk:

- SSE stream emits events a user should not see.

Mitigation:

- authenticate event stream
- server-side permission filtering
- no public visitor access to admin event stream
- event payload minimization

### Event replay / duplicate actions

Risk:

- repeated events cause duplicate UI actions or unsafe state changes.

Mitigation:

- event id deduplication
- idempotent cache update handlers
- mutation actions remain explicit user/API calls, not automatic from events

## WebChat widget isolation threats

### Host CSS breaks widget

Risk:

- host website global CSS changes widget layout.

Mitigation:

- Shadow DOM isolation
- internal CSS reset
- component-scoped styles

### Widget CSS breaks host page

Risk:

- widget global styles affect host page.

Mitigation:

- no document-global CSS except minimal launcher mount if unavoidable
- Shadow DOM styles only

## File/attachment threats

Risk:

- malicious uploads, unsafe previews, content sniffing, XSS through filenames.

Mitigation:

- backend file validation remains required
- frontend displays sanitized filenames
- previews only for safe MIME types
- no raw HTML rendering from attachments

## Logging and telemetry threats

Risk:

- logs expose customer data, tokens, secrets, or AI prompts.

Mitigation:

- redact tokens and secrets
- avoid full payload logging in browser
- log identifiers and safe status fields where possible
- separate debug mode from production mode

## Security acceptance checklist

- Public WebChat APIs do not expose internal ticket ids.
- Visitor token cannot access admin APIs.
- Admin-only UI actions are backed by server authorization.
- AI suggestions are visibly separate from verified facts.
- Safety gate blocks sensitive or unsupported content.
- Widget does not pollute host page CSS.
- Realtime stream is authenticated and permission filtered.
- No tokens are logged in browser console or UI errors.
- Dangerous actions require confirmation or explicit review where appropriate.
