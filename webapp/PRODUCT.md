# Nexus OSR Frontend Product Register

## Product identity

Nexus OSR is a **case-resolution cockpit for multi-country logistics operations** with one bounded administration control plane.

It is not primarily a chatbot, a WebChat inbox, a Knowledge CMS, a Runtime console, or a generic administration dashboard. Those capabilities support the operator journey; they do not define it. Identity and access administration exists only to establish who may perform that journey, within which scope, and under which auditable authority.

The product helps an authorized operator establish the case, inspect authoritative evidence, take ownership, perform governed actions, understand the operational and customer outcome, and determine whether the case is blocked, under observation, eligible to close, safely closed, or must reopen.

## Primary product job

For every customer contact or governed operational signal, the frontend must make five answers clear:

1. **What is this case?**
2. **Which evidence is authoritative?**
3. **What must happen next, and who owns it?**
4. **What actually happened after an action was requested?**
5. **Can this case be safely completed, observed, repaired, or reopened?**

The canonical operator journey is:

`Login → Scoped queue → Case → Facts and policy → Ownership → Governed action → Operational result → Customer communication → Closure target → Observation or reopen`

The canonical identity lifecycle is:

`Administrator creates identity → role/team/capability projection → forced initial password rotation → authenticated session → permission or account change → auditable session revocation`

## Users

### Support Agent

- Works from the scoped operator queue.
- Reviews customer messages, authoritative facts, approved Knowledge, risk and missing information.
- Accepts ownership or handoff.
- Performs permitted actions and communicates through governed channels.
- Does not infer business closure from a technical status.

### Team Lead

- Monitors unowned work, SLA risk, escalations and repair-required cases.
- Takes over, assigns, releases or reroutes work through governed commands.
- Reviews blocked closure and repeated-contact patterns.

### Operations Manager

- Reviews workload, action effectiveness, closure quality and country/channel performance.
- Uses management evidence without replacing operational source truth.

### Knowledge and SOP Steward

- Maintains approved customer-visible Knowledge and internal operating guidance.
- Cannot override live facts, action authority or case closure.

### Channel Administrator

- Manages channel/account configuration and health.
- Does not gain case access solely from channel configuration permission.

### Runtime and Audit Operator

- Inspects bounded Runtime, debug, evaluation and audit evidence.
- Technical access does not imply customer-data or operational-action authority.

### Identity Administrator

- Creates, updates, activates and deactivates operator identities.
- Assigns canonical role profiles, team membership and explicit capability overrides.
- Resets credentials, requires password rotation and revokes sessions.
- Cannot bypass the final-active-admin, self-deactivation or backend authorization safeguards.

### Security Auditor

- Reviews effective capabilities, high-risk overrides and administrator actions.
- Read-only audit access does not imply identity mutation authority.

## Canonical route domains

| Domain | Route | Job |
|---|---|---|
| Authentication | `/login` | Establish operator identity |
| Account security | `/account` | Self-service password rotation and session revocation |
| Operator work | `/workspace` | Queue, case, evidence, ownership, action, communication and closure target |
| Knowledge and SOP | `/knowledge` | Govern Knowledge and internal operating guidance |
| Channels | `/channels` | Channel/account configuration and health |
| Runtime and audit | `/runtime` | Technical readiness, debug/eval and bounded runtime evidence |
| Management | `/control-tower` | Tenant-scoped workload, risk, outcome and drill-down |
| Administration | `/administration` | Users, roles, teams, capabilities, identity security and audit |

`/webchat` is a compatibility redirect only. It does not mount a second product surface.

Navigation is derived from backend capabilities and canonical scope. A hidden route or disabled button never substitutes for backend authorization. `/account` is available from the single account menu rather than duplicated in the primary operational navigation.

## Identity and access model

- `users` remains the sole credential and operator identity authority.
- `UserRole` and `ROLE_CAPABILITIES` remain the sole standard role-profile authority.
- `user_capability_overrides` remains the sole per-user permission override authority.
- `user_security_states` is the sole mutable session-version, forced-password-rotation and bounded login-metadata authority.
- `/api/admin/users` remains the sole user CRUD authority.
- `/api/auth/change-password` is the sole self-service password-change authority.
- Every password reset, password change, deactivation or explicit session revocation invalidates older access tokens through the canonical session version.
- Existing tokens issued before the session-version migration are interpreted as version 1 to avoid an uncontrolled global logout during deployment.
- New administrator-created identities must rotate the issued password before entering another protected product domain.
- Frontend capability checks only control presentation. Backend capability checks remain authoritative.

## Operator work model

The primary object is a **case**, opened from a canonical queue identity. A case may link Ticket, Handoff, conversation, Dispatch and other channel/source records, but no individual source record becomes a second case truth.

The Workspace must visibly separate:

- authoritative evidence;
- customer claim;
- approved Knowledge or policy;
- AI recommendation or prior AI output;
- human decision;
- system event;
- action outcome;
- customer-notification receipt;
- closure and observation state.

## Product vocabulary

### Evidence

Use:

- Authoritative and current
- Stale
- Unavailable
- Contradictory
- Customer claim
- Approved Knowledge/policy
- AI recommendation/history

Do not label short-lived Case Context as customer memory. **No C-end long-term customer memory** is permitted.

### Ownership

Use:

- Unassigned
- Assigned
- Handoff requested
- Handoff accepted
- Waiting for customer
- Waiting for operations

### Action and business result

Keep these distinct:

- Requested
- Accepted
- Technical completion
- Operational completion
- Customer notified
- Business result confirmed
- Repair required

An API success, queued Job, Job `done`, message `sent`, or Dispatch `dispatched` is not business result confirmation.

### Closure

Use:

- Closure blocked
- Observation period
- Eligible to close
- Safely closed
- Reopened

Ticket `resolved` or `closed` is a source status. It must not be presented as safely closed without the active scenario, required action outcomes, customer-notification policy and lifecycle evidence.

## Information hierarchy

For operator work, show information in this order:

1. Case identity, scope, risk and ownership.
2. Closure target and the missing requirement that currently blocks progress.
3. Authoritative evidence and conflicting/customer-supplied information.
4. Next permitted action and its confirmation requirement.
5. Current action/outcome state.
6. Customer conversation and communication composer.
7. Technical evidence behind progressive disclosure.

For identity administration, show information in this order:

1. Identity, active state, standard role and team.
2. Effective capabilities and explicit overrides.
3. Password-rotation and latest-login state.
4. Safe administrative actions with self/last-admin safeguards.
5. Resulting audit evidence.

Runtime model identity, raw Job identifiers and implementation traces are not primary operator content. They belong in bounded technical detail or the Runtime domain.

## Product behavior principles

- One primary action per current task state.
- Server-calculated permissions and action availability.
- UI success only after durable backend confirmation.
- No false success language.
- Empty states teach the next valid action.
- Errors state what failed and what the operator can do.
- Degraded, unavailable, stale, conflict and repair-required are first-class states.
- Refresh preserves durable state and never duplicates commands.
- Credential changes revoke prior sessions rather than relying on client-side logout.
- Role templates and explicit overrides are shown as distinct concepts.
- Keyboard operation and screen-reader structure are part of product behavior.

## Non-goals

- No direct Provider execution from UI code.
- No second queue, case truth or action truth.
- No second user-management API or parallel administration application.
- No client-authoritative permission or session state.
- No probabilistic silent cross-channel merge.
- No raw tracking/contact/provider identifiers on unsafe surfaces.
- No customer-visible reply bypass.
- No autonomous refund, compensation, legal, identity or funds action.
- No technical-status-as-closure language.
- No C-end long-term customer memory.

## Current implementation authority

- `webapp/src/routes/` contains the only route registry.
- `/workspace` is the only queue, case, conversation and governed-action surface.
- `/administration` is the only user, role, capability and identity-audit surface.
- `/account` is the only operator password and session self-service surface.
- `KnowledgePage.tsx` is the only Knowledge implementation; capability controls editing.
- `identityApi.ts` is the only identity-specific frontend API client and delegates transport to `apiClient.ts`.
- `apiClient.ts` is the only generic HTTP transport.
- `AppShell.tsx` and `navigation.ts` remain the only application-shell and navigation authorities.
- Material UI, one Nexus theme and one bounded operator-presentation module are the only generic visual authorities.
- `/webchat` redirects to canonical routes and does not own a product UI.

New work must extend these authorities and remove any superseded path in the same delivery.
