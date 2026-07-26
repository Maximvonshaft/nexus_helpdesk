# Nexus OSR Frontend Product Register

## Product identity

Nexus OSR is a **conversation-first logistics customer-operations cockpit**.

It is not primarily a chatbot, a WebChat inbox, a Knowledge CMS, a Runtime console, an automatic-handling builder, or a generic administration dashboard. Those capabilities support the operator journey; they do not define it.

The product helps an authorized operator understand a live customer conversation, establish durable responsibility only when necessary, inspect authoritative evidence, take ownership, perform governed actions, verify operational and customer outcomes, and decide whether the work can finish live, must become a durable case, is blocked, requires observation, is safely closed, or must reopen.

## Canonical business objects

Nexus uses one explicit business-object model. `docs/architecture/adr-001-ticket-as-case-authority.md` and `config/architecture/business-aggregate-authority.v1.json` are the governing contracts.

### Conversation

`WebchatConversation` is the live communication identity.

- A Conversation may include text, Voice, Agent turns and human participation.
- It may be completed without creating durable work.
- A live contact without a Ticket is shown as a **会话**.
- Conversation snapshots do not create a second ownership, Handoff or closure authority.

### Ticket-as-Case

`Ticket` is the sole durable **Ticket-as-Case** aggregate.

- A Ticket is created or reused only for asynchronous follow-up, governed business action, regulatory record or explicitly accepted durable responsibility.
- `Ticket.id` is the stable Case identity; APIs may expose it as `case_id` without creating a parallel identifier.
- Durable ownership, SLA snapshot, business action/outcome ledger and Safe Effective Closure belong to Ticket-as-Case.
- No `Case` ORM model, `cases` table or second Case lifecycle is permitted.

### Handoff and queue

`WebchatHandoffRequest` is the sole mutable live human-Handoff lifecycle.

`OperatorTask` is a rebuildable read projection. It supports queue ordering and presentation but is never allowed to mutate source-domain state. Assignment, release, close and resume commands must call the corresponding source aggregate service.

## Primary product job

For a live Conversation, the frontend must make five answers clear:

1. What is the customer asking and which facts are authoritative?
2. Can the interaction be completed live?
3. Is human participation or durable follow-up required?
4. What customer-visible terminal outcome was committed?
5. Has capacity been released safely?

For a Ticket-as-Case, the frontend must make five answers clear:

1. What durable responsibility exists?
2. Which evidence and policy are authoritative?
3. What must happen next, and who owns it?
4. What actually happened after each governed action?
5. Can the Case be observed, repaired, safely closed, or reopened?

The canonical journey is:

`Password login → MFA when enabled → Credential recovery when required → Scoped queue → Conversation → live resolution OR Ticket-as-Case → evidence and policy → ownership → governed action → operational result → customer communication → closure assessment → observation or reopen`

## Users

### Support Agent

- Works from the scoped operator queue.
- Handles live Conversations and durable Ticket-as-Case records without conflating them.
- Reviews customer messages, authoritative facts, approved Knowledge, risk and missing information.
- Accepts Handoff or durable Case ownership through governed commands.
- Performs permitted actions and communicates through governed channels.
- Does not infer business closure from a technical status.

### Team Lead

- Monitors unowned work, Handoff wait, capacity, SLA risk, escalations and repair-required Cases.
- Takes over, assigns, releases or reroutes work through source-domain commands.
- Reviews blocked closure and repeated-contact patterns.

### Operations Manager

- Reviews workload, action effectiveness, Safe Effective Closure, reopen, repeat contact, provider quality and country/channel performance.
- Uses management projections without replacing operational source truth.

### Knowledge and SOP Steward

- Maintains approved customer-visible Knowledge and internal operating guidance.
- Cannot override live facts, action authority or Case closure.

### Automatic-handling Administrator

- Configures handling plans, reply style, business rules, Tool permissions, integrations, model settings and runtime limits through `/agent-control`.
- Works with published versions and explicit effective scope.
- Uses bounded diagnostics without gaining Case, customer-data or business-action authority automatically.

### Channel Administrator

- Manages channel/account configuration and health.
- Does not gain Case access solely from channel configuration permission.

### Identity Administrator

- Manages users, capabilities, teams, credential policy, two-step-verification recovery and account sign-out.
- Cannot create a second user directory, permission table, session store, MFA store or authentication path.

### Runtime and Audit Operator

- Inspects bounded runtime, diagnostic, evaluation and audit evidence.
- May explicitly restore failed background tasks or outbound records only with the corresponding permission.
- Technical access does not imply customer-data or operational-action authority.

## Canonical route domains

| Domain | Route | Job |
|---|---|---|
| Authentication | `/login` | Establish password identity and complete two-step verification when enabled |
| Account | `/account` | Password, two-step verification, recovery codes and signed-in devices |
| Operator work | `/workspace` | Conversation, Ticket-as-Case, evidence, ownership, governed action, communication and closure |
| Knowledge and SOP | `/knowledge` | Govern Knowledge and internal operating guidance |
| Automatic handling | `/agent-control` | Configure handling plans, reply style, rules, Tools, integrations, limits and effective scope |
| Channels | `/channels` | Channel onboarding, health and account governance |
| Runtime and audit | `/runtime` | Technical readiness, bounded evidence, queue health and audited recovery |
| Management | `/control-tower` | Tenant-scoped workload, business outcome, risk, trend and drill-down |
| Identity administration | `/administration` | Users, capabilities, teams, credentials and security audit |

`/webchat` is a compatibility redirect only. It does not mount a second operator product.

Navigation is derived from backend permissions and canonical scope. A hidden route or disabled button never substitutes for backend authorization.

## Operator language model

Primary surfaces are organized by the operator's task rather than by service, database or runtime implementation objects.

Primary content may show only:

- task or object identity;
- whether it is a Conversation or Ticket-as-Case;
- current business state;
- relevant facts and scope;
- blocking reason;
- recovery step;
- explicit action;
- confirmed result.

Implementation architecture, raw identifiers, authorization codes, payloads, traces, protocol details and model internals belong in named progressive disclosures such as `系统信息`, `技术详情`, `权限代码`, `运行证据` or `连接详情`.

Use business labels such as:

- `自动处理` rather than `Agent 控制`;
- `处理方案` rather than `Agent Definition`;
- `回复风格` rather than `Persona`;
- `业务规则` rather than `Business Playbook`;
- `生效范围` rather than `Deployment`;
- `运行记录` rather than `Agent Run Explorer`.

The UI must not narrate internal authority, frontend/backend responsibility, singleton implementation, control-plane topology or policy-code names on a primary surface.

## Identity, credential and tenant authority

- `User` is the only operator identity record.
- `User.updated_at` and the server capability fingerprint are the token-freshness authorities.
- MFA secrets and recovery codes use purpose-specific encryption/hashing and never enter audit payloads.
- Tenant ownership is derived from the authenticated server principal and canonical relations.
- The browser never supplies or overrides tenant identity.
- Tenant-owned resources must resolve through the relational Tenant authority; external `tenant_key` is a stable boundary key, not an alternate owner.

## Automatic-handling configuration model

- `/agent-control` is the only automatic-handling configuration, version, effective-scope and bounded diagnostic surface.
- Saving a draft does not change live handling.
- Publishing creates an immutable version; applying it to scope is a separate explicit action.
- Reapplying a prior release is the rollback path and does not copy configuration.
- Preview, validation, queued processing, execution and customer outcome are distinct states.

## Runtime recovery model

- `/runtime` is the only runtime-health and failed-task-recovery surface.
- Recovery reuses existing rate-limited, audited backend authorities.
- Recovery success means only that processing was rescheduled; it never means operational or business completion.
- Every accepted public message must converge to exactly one customer-visible terminal outcome or an intentional suppression caused by a newer message or committed human ownership.

## Channel account model

- `/channels` is the only channel onboarding, health and account-governance surface.
- Credentials are write-only encrypted fields.
- Enabling an account does not substitute for a successful test and capability-specific production evidence.
- Historical messages and audit evidence remain governed after account disablement.

## Operator work model

The Workspace supports two related but distinct modes:

1. **Conversation mode** — live interaction, AI/human participation, Handoff and immediate completion.
2. **Ticket-as-Case mode** — durable responsibility, SLA, governed actions, structured outcomes, customer notification, closure assessment and reopen.

The Workspace must visibly separate:

- authoritative evidence;
- customer claim;
- approved Knowledge or policy;
- AI recommendation or prior AI output;
- human decision;
- system event;
- action intent and execution attempt;
- Provider receipt and operational outcome;
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

- Conversation unassigned
- Handoff requested
- Handoff accepted
- Case unassigned
- Case assigned
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

An API success, queued Job, Job `done`, message `sent`, Dispatch `dispatched`, test email success, publication request or failed-record recovery is not business result confirmation.

### Closure

Use:

- Live conversation completed
- Durable Case required
- Closure blocked
- Observation period
- Eligible to close
- Safely closed
- Reopened

Ticket `resolved` or `closed` is a source status. It must not be presented as safely closed without the active scenario, required action outcomes, notification policy and lifecycle evidence.

## Information hierarchy

For a live Conversation:

1. Conversation identity, scope, customer request and Handoff state.
2. Authoritative evidence and missing information.
3. Next permitted live action.
4. Customer-visible terminal outcome.
5. Ticket creation only when durable responsibility is required.

For Ticket-as-Case:

1. Case identity, scope, risk and ownership.
2. Closure target and the missing requirement that blocks progress.
3. Authoritative evidence and conflicting/customer-supplied information.
4. Next permitted action and confirmation requirement.
5. Action, Provider, operational and notification outcomes.
6. Customer conversation and composer.
7. Technical evidence behind progressive disclosure.

For configuration and administration:

1. Object or task identity and current state.
2. Scope and affected users/channels/markets.
3. Required business fields.
4. Primary save, publish, apply, test, recover or confirm action.
5. Operational result and next step.
6. Advanced protocol, model, permission or audit evidence behind progressive disclosure.

## Product behavior principles

- One primary action per current task state.
- Server-calculated permissions, tenant scope and action availability.
- UI success only after durable backend confirmation.
- No false success language and no silent accepted-message terminal state.
- Empty states teach the next valid action.
- Degraded, unavailable, stale, conflict and repair-required are first-class states.
- Refresh preserves durable state and never duplicates commands.
- Keyboard operation and screen-reader structure are part of product behavior.
- Sensitive account, publication, scope, recovery and disablement operations require explicit confirmation and durable audit.
- Read projections may be rebuilt and may never own source-domain commands.

## Non-goals

- No direct Provider execution from UI code.
- No second queue, Case truth, Conversation truth, Handoff truth or action truth.
- No second automatic-handling builder, user directory, RBAC table, session store, MFA store, channel product, Runtime product or authentication transport.
- No client-owned tenant scope.
- No probabilistic silent cross-channel merge.
- No raw tracking/contact/provider identifiers on unsafe surfaces.
- No customer-visible reply bypass.
- No autonomous refund, compensation, legal, identity or funds action.
- No technical-status-as-closure language.
- No C-end long-term customer memory.
- No implementation architecture as primary operator copy.

## Current implementation authority

- `webapp/src/routes/` contains the only route registry.
- `/workspace` is the only Conversation, Ticket-as-Case, queue, evidence and governed-action surface.
- `/knowledge` and `KnowledgePage.tsx` are the only Knowledge implementation.
- `/agent-control` is the only automatic-handling configuration and diagnostic surface.
- `/account` is the only current-user credential surface.
- `/administration` is the only user, role/capability, team and security-audit surface.
- `/channels` is the only channel onboarding, health and account-governance surface.
- `/runtime` is the only runtime evidence, queue health and recovery surface.
- `apiClient.ts` is the only generic HTTP transport; domain APIs are typed adapters over it.
- Material UI, one Nexus theme and one bounded operator-presentation module are the only generic visual authorities.
- `webapp/design/operator-language.v1.json` is the operator-language authority.
- `/webchat` redirects to canonical routes and does not own a product UI.

New work must extend these authorities and remove any superseded path in the same delivery.
