# 16 — Rollback Plan

## Status

Proposed. This document defines rollback expectations for the frontend upgrade after execution-readiness approval.

## Rollback principle

Every production behavior change must be reversible or safely disable-able. If a rollback path is unclear, the change is not ready for production.

## Rollback layers

Rollback can happen at these layers:

1. PR revert
2. frontend build artifact rollback
3. feature flag disablement
4. WebChat widget artifact rollback
5. realtime fallback to polling
6. AI Governance revert to previous published config/version
7. backend image rollback if backend changed
8. database rollback only through an explicit audited plan

## Docs-only planning PR rollback

For PR #26 and this readiness package:

- runtime rollback is not required because no production code changes are made
- rollback is a git revert of documentation commits if needed
- no backend, frontend build, API, or database rollback is involved

## Frontend console rollback

For console-only changes:

Preferred rollback options:

1. Disable feature flag if available.
2. Revert PR and redeploy previous frontend build.
3. Restore previous `frontend_dist` artifact if release process stores artifacts.

Required before release:

- previous known-good commit or artifact identified
- smoke checklist for restored build available

## Workspace rollback

Workspace is high risk because it is the main operator workflow.

Rollback options:

1. Feature flag back to old Workspace internals if implemented.
2. Revert Workspace cockpit PR.
3. Redeploy previous frontend build.

Workspace-specific rollback triggers:

- ticket list fails to load
- ticket detail fails to load
- workflow update fails
- dirty-state protection causes data loss
- agents cannot complete normal ticket handling

## WebChat widget rollback

WebChat rollback must be stricter because external websites may embed `widget.js`.

Rollback options:

1. Serve previous `widget.js` artifact.
2. Keep versioned widget artifacts and repoint stable `/webchat/widget.js` to known-good version.
3. Disable new SDK path through feature flag if dual-run is implemented.

Required before WebChat SDK release:

- previous widget artifact stored
- old snippet compatibility smoke passed
- public WebChat API compatibility confirmed
- cache invalidation strategy documented if CDN/proxy caching exists

WebChat rollback triggers:

- widget launcher does not appear
- init fails for existing snippet
- visitor message send fails
- visitor conversation cannot resume after reload
- widget breaks host page layout
- widget exposes internal ticket ids or admin data

## Realtime rollback

Realtime must be additive. Polling must remain available until realtime has proven stable.

Rollback options:

1. Disable realtime feature flag.
2. Force fallback polling.
3. Revert event-client integration PR.

Realtime rollback triggers:

- event stream causes auth errors
- duplicate events corrupt UI state
- event stream leaks unauthorized data
- realtime failure blocks ticket/WebChat usage
- backend load increases unexpectedly

## AI Governance rollback

AI Governance rollback has two meanings: UI rollback and published config rollback.

### UI rollback

Options:

1. Disable new Governance Studio UI if feature-flagged.
2. Revert PR to old AI Control page.
3. Redeploy previous frontend build.

### AI config rollback

Options:

1. Use version rollback API to publish previous known-good config snapshot.
2. Disable problematic config if supported.
3. Revert to global safe fallback policy.

AI rollback triggers:

- invalid config can be published
- draft/published states are confused
- AI output bypasses safety gate
- AI suggests unsupported logistics factual promises
- published config breaks Workspace Copilot behavior

## Runtime Control rollback

Options:

1. Disable new control tower view.
2. Revert runtime UI PR.
3. Keep dangerous actions hidden/disabled until confirmed.

Rollback triggers:

- runtime page causes auth loop
- replay/drop actions are exposed without confirmation
- health state is misleading
- operators cannot identify degraded state

## Backend/API rollback

If a frontend epic requires backend API changes, a separate backend rollback plan is required.

Rules:

- API changes should be backward compatible where possible.
- New fields can be additive.
- Removing or renaming fields requires compatibility period.
- Backend image rollback must preserve database compatibility.

## Database rollback

Database rollback is not allowed as an implicit side effect of frontend upgrade.

Rules:

- No schema changes inside frontend-only PRs.
- Any migration must have separate review.
- Destructive downgrade should not be automatic in production.
- If production data is written, rollback must be explicit and audited.

## Release artifact retention

For high-risk phases, retain:

- previous frontend build artifact
- previous WebChat widget artifact
- previous Docker image tag if backend touched
- previous AI published config version
- PR and commit SHAs

## Rollback smoke checklist

After rollback:

```text
[ ] /healthz OK
[ ] /readyz OK
[ ] Login works
[ ] Workspace opens
[ ] Ticket detail opens
[ ] WebChat admin opens
[ ] WebChat widget opens if affected
[ ] Visitor message send works if affected
[ ] AI Control / Governance opens if affected
[ ] Runtime opens if affected
[ ] No critical browser console errors
```

## Rollback acceptance

Rollback is accepted only when the impacted user flow returns to the previous known-good behavior and no new critical errors are observed.

## Final rule

If a phase cannot name its rollback path, it must not be released.
