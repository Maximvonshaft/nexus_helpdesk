# Nexus OSR Frontend Product Register

## Product identity

Nexus OSR is a **case-resolution cockpit for multi-country logistics operations**.

It is not primarily a chatbot, a WebChat inbox, a Knowledge CMS, a Runtime console, or a generic administration dashboard. Those capabilities support the operator journey; they do not define it.

The product helps an authorized operator establish the case, inspect authoritative evidence, take ownership, perform governed actions, understand the operational and customer outcome, and determine whether the case is blocked, under observation, eligible to close, safely closed, or must reopen.

## Primary product job

For every customer contact or governed operational signal, the frontend must make five answers clear:

1. **What is this case?**
2. **Which evidence is authoritative?**
3. **What must happen next, and who owns it?**
4. **What actually happened after an action was requested?**
5. **Can this case be safely completed, observed, repaired, or reopened?**

The canonical journey is:

`Login → Scoped queue → Case → Facts and policy → Ownership → Governed action → Operational result → Customer communication → Closure target → Observation or reopen`

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

- Maintains approved customer-visible Knowledge and, after M11, internal SOP skills.
- Cannot override live facts, action authority or case closure.

### Channel Administrator

- Manages channel/account configuration and health.
- Does not gain case access solely from channel configuration permission.

### Runtime and Audit Operator

- Inspects bounded Runtime, debug, evaluation and audit evidence.
- Technical access does not imply customer-data or operational-action authority.

### Identity and Security Administrator

- Creates, updates, activates and deactivates operator accounts through the canonical Administration domain.
- Assigns server-defined roles, effective capabilities, teams and work-scope boundaries.
- Resets credentials through audited commands; password and identity changes revoke stale sessions.
- Cannot bypass backend authorization, last-admin protection, audit redaction or team lifecycle safeguards.

## Canonical route domains

| Domain | Route | Job |
|---|---|---|
| Authentication | `/login` | Establish operator identity |
| Operator account | `/account` | Inspect current identity and rotate the current user's password |
| Operator work | `/workspace` | Queue, case, evidence, ownership, action, communication and closure target |
| Knowledge and SOP | `/knowledge` | Govern Knowledge and internal operating guidance |
| Channels | `/channels` | Channel/account configuration and health |
| Runtime and audit | `/runtime` | Technical readiness, debug/eval and bounded runtime evidence |
| Management | `/control-tower` | Tenant-scoped workload, risk, outcome and drill-down |
| Administration | `/administration` | User, role, capability, team, work-scope and security-audit governance |

`/webchat` is a compatibility redirect only. It does not mount a second product surface.

Navigation is derived from backend capabilities and canonical scope. A hidden route or disabled button never substitutes for backend authorization. `/account` is reached from the authenticated identity area rather than duplicating the primary business navigation.

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
- Keyboard operation and screen-reader structure are part of product behavior.
- Credential, role, team, status and capability changes invalidate stale authenticated sessions.
- Administrative mutation controls are absent in read-only audit mode and remain backend-authorized when present.

## Non-goals

- No direct Provider execution from UI code.
- No second queue, case truth or action truth.
- No second admin product, user-management service, role policy table or session store.
- No probabilistic silent cross-channel merge.
- No raw tracking/contact/provider identifiers on unsafe surfaces.
- No customer-visible reply bypass.
- No autonomous refund, compensation, legal, identity or funds action.
- No technical-status-as-closure language.
- No C-end long-term customer memory.

## Current implementation authority

- `webapp/src/routes/` contains the only route registry.
- `/workspace` is the only queue, case, conversation and governed-action surface.
- `/account` is the only current-user credential surface.
- `/administration` is the only user, role, capability, team and security-audit governance surface.
- `KnowledgePage.tsx` is the only Knowledge implementation; capability controls editing.
- `apiClient.ts` is the only generic HTTP transport.
- Material UI, one Nexus theme and one bounded operator-presentation module are the only generic visual authorities.
- `/webchat` redirects to canonical routes and does not own a product UI.

New work must extend these authorities and remove any superseded path in the same delivery.
