# 06 — WebChat Runtime Blueprint

## Status

Proposed. This document defines the target WebChat architecture before implementation begins.

## Current state

Current WebChat has two sides:

1. Admin UI in `webapp/src/routes/webchat.tsx`.
2. Static visitor widget in `backend/app/static/webchat/widget.js`.

The visitor widget currently:

- runs as a self-invoking browser script
- reads `data-*` attributes from the script tag
- injects CSS into the host document head
- creates its own DOM button and panel
- stores `conversationId` and `visitorToken` in localStorage
- initializes through `/api/webchat/init`
- sends visitor messages through public WebChat API
- polls conversation messages while the panel is open

## Product goal

Upgrade WebChat from a basic embeddable chat entry point into a configurable, secure, structured interaction runtime.

Target description:

> A one-line embeddable customer support runtime that can be configured per tenant/channel, isolated from host websites, connected to NexusDesk tickets, and ready for AI-assisted structured interactions.

## Non-negotiable compatibility requirement

The one-line script embed contract must remain supported.

Existing style:

```html
<script src="https://YOUR_NEXUSDESK_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" async></script>
```

Any SDK refactor must preserve this integration path or provide a dual-run compatibility plan.

## Target package structure

```text
packages/
  webchat-core/
    src/
      apiClient.ts
      state.ts
      events.ts
      messageModel.ts
      config.ts
  webchat-widget/
    src/
      mount.ts
      shadowRoot.ts
      widgetApp.ts
      styles.ts
      transport.ts
      index.ts
  webchat-react/
    src/
      NexusWebChat.tsx
      useWebChat.ts
```

## Build model

- Use TypeScript.
- Use Vite library mode for `webchat-widget`.
- Emit browser-ready `widget.js` for current static serving path.
- Keep asset count minimal.
- Do not require host website bundler.
- Do not require host React.

## Shadow DOM requirement

The widget should use Shadow DOM to isolate:

- CSS reset
- component styles
- fonts where possible
- layout
- z-index behavior

Rules:

- Host page CSS must not break widget UI.
- Widget CSS must not affect host page UI.
- Host page should not need to load Tailwind or React.

## Widget configuration model

Script attributes should support at minimum:

- `data-api-base`
- `data-tenant`
- `data-channel`
- `data-title`
- `data-subtitle`
- `data-assistant-name`
- `data-locale`
- `data-welcome`
- `data-button-label`
- `data-close-label`
- `data-theme`

Future server-side channel config should support:

- allowed origins
- theme tokens
- operating hours
- offline form behavior
- welcome flow
- AI auto-reply policy
- human handoff rules
- upload permissions

## Security model

Visitor-side rules:

- Public API must never expose internal numeric ticket ids.
- Visitor reads require conversation id plus visitor token.
- Visitor token is separate from admin auth token.
- Message length limits must be enforced server-side and reflected client-side.
- Origin allowlist should be persisted and enforced in a later backend-reviewed phase.
- Widget should not log tokens or sensitive message content to console.

Admin-side rules:

- Admin WebChat APIs require authenticated admin token.
- Replies pass through safety gate.
- WebChat local ACK must remain semantically distinct from external channel provider dispatch.

## Structured interaction model

Future WebChat messages should support structured payloads while preserving plain text messages.

Target message types:

```text
text
quick_reply_group
tracking_lookup_card
tracking_status_card
pod_upload_card
contact_info_form
handoff_notice
sla_expectation_card
rating_card
system_notice
```

Each structured message should have:

- type
- stable id
- display payload
- optional action payload
- safe fallback text

## Transport model

Phase target:

1. Preserve current polling as fallback.
2. Add realtime-ready abstraction.
3. Later connect to SSE or WebSocket when backend event stream is ready.

Transport interface:

```text
initConversation()
sendMessage()
fetchMessages()
subscribeMessages()
close()
```

## Admin WebChat Control Center

The current admin page should evolve into a WebChat Control Center.

Target sections:

- Inbox
- Conversation detail
- Reply composer
- AI suggestion panel
- Safety gate result
- Widget channel settings
- Snippet generator
- Theme preview
- Origin allowlist status
- Runtime logs

## Snippet generator

Admin should generate copyable snippets for:

- default channel
- tenant-specific channel
- staging/testing channel
- localized widget
- custom title/welcome configuration

Snippet generator must clearly indicate:

- environment
- domain
- tenant
- channel
- allowed origins status

## Theme Studio

Target configuration:

- brand color
- panel radius
- launcher label
- header title/subtitle
- assistant name
- welcome text
- locale
- compact/comfortable density

## Migration plan

1. Add package structure and build pipeline while preserving old static output path.
2. Re-implement existing widget behavior in TypeScript.
3. Add Shadow DOM isolation.
4. Add config parser and strict config normalization.
5. Add compatibility smoke test for old snippet.
6. Add theme token support.
7. Add structured message rendering.
8. Add realtime transport abstraction with polling fallback.
9. Add admin snippet/theme/channel configuration UI.

## Rollback plan

- Keep the old `widget.js` artifact available during migration.
- New widget should be served behind a versioned static path when possible.
- If new widget fails smoke, production can point back to the previous artifact.
- Public API changes must be backward compatible.

## Acceptance criteria

- Existing snippet still initializes a conversation.
- Widget does not depend on host React.
- Widget does not pollute host CSS.
- Widget is usable on mobile viewport.
- Visitor conversation persists after reload.
- Visitor cannot access internal ticket ids.
- Admin can see WebChat conversation and reply.
- Safety gate remains active for admin replies.
- Widget build size is measured and documented.
