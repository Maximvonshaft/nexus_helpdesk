# WebChat AI Provider Router

Phase 1 adds a provider router for WebChat Fast Lane while preserving the current production default.

## Current default chain

```text
WebChat browser
-> /api/webchat/fast-reply
-> webchat_fast_ai_service.generate_webchat_fast_reply
-> provider_router.generate_fast_reply
-> openclaw_responses provider
-> existing OpenClaw Responses client
-> existing strict JSON parser
-> customer reply or handoff snapshot
```

## Available providers

- `openclaw_responses`: current default and existing production behavior.
- `codex_auth`: Phase 1 skeleton only; disabled by default and returns an explicit not-confirmed result until a real server-side transport is proven by the probe.
- `openai_responses`: Phase 1 skeleton only; reserved for a future OpenAI API implementation.

## Why this exists

The immediate goal is not to remove the existing OpenClaw path. The goal is to stop hard-coding one upstream path so Nexus can later select a safer cloud-side provider without rewriting Fast Lane again.

## Tool execution boundary

Native model tool/function calls remain rejected by the existing strict parser. Future tool hints must use controlled Nexus JSON and Nexus must validate and execute any action. A model must not write directly to the database.

## Rollback

Use the default provider settings and disable the experimental providers. This keeps the existing OpenClaw route as the active path.
