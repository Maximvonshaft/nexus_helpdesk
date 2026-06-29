# WebChat AI Provider Router

Phase 1 adds a provider router for WebChat Fast Lane. In production, the only supported WebChat Fast Reply entry point is `WEBCHAT_FAST_AI_PROVIDER=provider_runtime`.

## Production chain

```text
WebChat browser
-> /api/webchat/fast-reply
-> webchat_fast_ai_service.generate_webchat_fast_reply
-> provider_router.generate_fast_reply
-> provider_runtime
-> provider_routing_rules
-> codex_app_server
-> codex-app-server-bridge
-> codex-appserver-runtime
-> customer reply or handoff snapshot
```

## Available providers

- `provider_runtime`: required for production WebChat Fast Reply.
- `codex_auth`: deprecated legacy direct provider retained for development/test compatibility only.
- `codex_app_server`: deprecated legacy direct provider retained for development/test compatibility only.
- `openai_responses`: legacy direct provider retained for development/test compatibility only.
- `rule_engine`: deterministic non-AI fallback inside Provider Runtime.

## Why this exists

The immediate goal is to prevent production WebChat Fast Reply from bypassing Provider Runtime routing, auditing, canary, and credential controls while retiring the old OpenClaw direct provider path.

## Tool execution boundary

Native model tool/function calls remain rejected by the existing strict parser. Future tool hints must use controlled Nexus JSON and Nexus must validate and execute any action. A model must not write directly to the database.

## Rollback

Keep `WEBCHAT_FAST_AI_PROVIDER=provider_runtime` in production and roll back inside Provider Runtime routing rules, provider credentials, or the codex appserver bridge/runtime deployment. Do not switch production WebChat Fast Reply back to a legacy direct provider.
