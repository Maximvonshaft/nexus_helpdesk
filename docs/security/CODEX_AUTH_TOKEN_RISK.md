# Codex Auth Provider Risk Notes

Phase 1 treats Codex authorization as an experimental server-side provider path, not as a confirmed production transport.

## Boundaries

- Do not expose provider credentials to browsers or customer messages.
- Do not place provider credentials in tickets, comments, events, or debug payloads.
- Do not use browser-session scraping or cookie extraction.
- Do not let customers start an authorization flow.
- Do not assume a Codex authorization credential is equivalent to a standard API key.

## Phase 1 state

This branch only adds:

- provider routing,
- a conservative transport probe,
- disabled-by-default provider skeletons,
- secret redaction helpers,
- tests for feature gates and no-leak behavior.

It does not add a production credential vault.

## Required later controls

Phase 2 must add encrypted credential storage, admin-only import, rotation, revoke/disable metadata, usage audit, provider kill switch, and canary routing before real traffic is sent to a Codex-based provider.
