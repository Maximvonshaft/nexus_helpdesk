# Chatwoot Fork Strategy: Merge NexusDesk Strengths into Chatwoot

Issue: #237

Status: strategic correction/addendum to `CHATWOOT_AUDIT_AND_BRIDGE_POC.md`.

## Executive Correction

A more commercially realistic path is not only "Chatwoot as a sidecar for NexusDesk". If the product goal is to build a mature, production-usable, commercially defensible customer-operations platform quickly, the stronger direction is:

```text
Chatwoot fork / Chatwoot-derived product shell
        +
NexusDesk logistics, AI, governance, WebChat Fast, WebCall, and Speedaf workflow modules
        =
Logistics-native omnichannel AI support platform
```

In that model, Chatwoot supplies the mature support platform shell:

- omnichannel inbox;
- agent conversation workspace;
- contact/conversation/message primitives;
- web widget;
- API channel;
- webhook system;
- help center / saved replies / assignment workflows;
- production-grade customer-support UX conventions.

NexusDesk supplies the domain-specific differentiators:

- logistics operational case model;
- parcel/tracking/POD/delivery-exception semantics;
- Speedaf integration paths;
- AI/OpenClaw/Codex runtime orchestration;
- fact-gated tracking replies;
- idempotent WebChat Fast Lane;
- WebCall voice lifecycle;
- admin audit/governance;
- market/channel-account routing;
- work-order creation;
- customer-visible logistics policy enforcement.

This is the better direction if the priority is fast commercialization and a strong customer-service product surface.

## Why This Direction May Be Better

### 1. Chatwoot Already Solves the Hard Generic Helpdesk Layer

Nexus should not spend months rebuilding:

- agent inbox layout;
- conversation filters;
- assignment/team UX;
- reply composer;
- private notes;
- message status;
- channel limitations;
- widget runtime;
- canned replies / help center;
- webhook/API channel management;
- contact profile UI.

Those are generic customer-support capabilities. Chatwoot already has a mature structure for them.

### 2. Nexus Has the Valuable Logistics and AI Intelligence Layer

The current Nexus codebase has domain capabilities Chatwoot does not have:

- `Ticket` includes logistics fields such as `tracking_number`, `case_type`, `issue_summary`, `customer_request`, `required_action`, `missing_fields`, `preferred_reply_channel`, `source_dedupe_key`, `market_id`, `country_code`, and `channel_account_id`.
- `WebchatConversation` and `WebchatMessage` support deterministic WebChat Fast identity, AI turns, safety metadata, ticket linkage, and card actions.
- `webchat_fast.py` handles CORS, origin validation, rate limiting, idempotency, tracking fact lookup, support-hours policy, AI reply generation, handoff policy, ticket creation, and Speedaf work-order enqueueing.
- `webchat_fast_idempotency_db.py` already has strict customer-action idempotency and retry semantics.
- `integration.py` has authenticated, rate-limited, idempotent integration endpoints for profile lookup and task creation.
- OpenClaw/Codex runtime work creates an AI provider path that is much deeper than ordinary answer suggestion.

These are precisely the pieces that should be merged into a Chatwoot-derived product to make it logistics-native.

## Target Architecture: Chatwoot as Shell, Nexus as Domain Engine

```text
Chatwoot Fork / Derived Product
  - Agent Inbox
  - Contact Profile
  - Conversation Timeline
  - Reply Composer
  - Web Widget
  - API Channel
  - Webhooks
  - Help Center
  - Assignment / Labels / Teams

Nexus Domain Engine Modules
  - Logistics Case Engine
  - Tracking Fact Engine
  - POD / Evidence Model
  - Speedaf Connector
  - AI/OpenClaw/Codex Runtime
  - WebChat Fast Policy Engine
  - WebCall Voice Lifecycle
  - Governance / Audit / Admin Rules
  - Market / Country / Channel Routing

Integration Boundary Inside Fork
  - Rails/Vue Chatwoot shell calls Nexus domain APIs/services
  - domain data gradually embedded into Chatwoot UI panels
  - high-risk AI/runtime code can remain a separate service at first
```

## What Should Be Merged from Nexus into Chatwoot

### Tier 1 — Highest Value, Merge First

#### 1. Logistics Case Sidebar for Chatwoot Conversation

Add a Nexus-style logistics panel to the Chatwoot conversation detail view.

Fields:

- tracking number;
- current parcel status;
- delivery attempt status;
- exception type;
- required action;
- missing fields;
- customer request;
- SLA deadline;
- market/country;
- preferred reply channel;
- linked Nexus ticket/case reference.

Chatwoot value added:

- Agents stop treating every conversation as pure chat.
- A logistics case becomes visible beside the conversation.
- Chatwoot becomes a delivery support cockpit, not just a message inbox.

#### 2. Tracking Fact Lookup and Fact-Gated Reply

Port Nexus tracking fact logic into Chatwoot as a backend service or external domain-engine call.

Initial behavior:

- detect tracking number in conversation messages;
- query logistics/tracking fact service;
- show safe tracking fact summary in conversation side panel;
- draft customer reply only when evidence is present and PII is redacted;
- otherwise escalate or ask for missing tracking number.

This is a major differentiator over standard Chatwoot AI.

#### 3. AI/OpenClaw Reply Runtime as Agent Assist

Do not depend on Chatwoot Enterprise Captain as the core AI layer. Use Nexus AI/OpenClaw/Codex runtime as a fork-side integration.

Agent UI:

- "Generate logistics reply";
- "Summarize parcel issue";
- "Recommend next operational action";
- "Create Speedaf work order";
- "Detect missing fields";
- "Human handoff required / not required".

Backend:

- keep Codex/OpenClaw token/runtime code isolated;
- expose it through a narrow internal service contract;
- log AI decision, tool calls, and fact evidence.

#### 4. Idempotency and Event Governance

Port Nexus idempotency discipline into Chatwoot side effects.

Apply to:

- webhook ingestion;
- outbound message creation;
- AI turn generation;
- work-order creation;
- ticket/escalation creation;
- tracking lookup action.

This reduces duplicate tickets, duplicate replies, duplicate work orders, and unsafe retries.

#### 5. Speedaf Work-Order Creation

Add a logistics action button in Chatwoot conversation:

- create delivery follow-up work order;
- create redelivery task;
- create lost/damaged parcel investigation;
- escalate to local market team;
- attach conversation summary.

Nexus already has a work-order enqueue pattern in `webchat_fast.py` via `enqueue_speedaf_work_order_create_job`.

### Tier 2 — Merge After First Product Loop

#### 6. WebCall Voice Lifecycle

Chatwoot has some voice/WhatsApp call concepts, but Nexus WebCall work is more aligned with the user's logistics support scenario.

Port into fork as:

- customer web call entry;
- agent incoming call queue;
- accept/reject/hangup lifecycle;
- call session attached to conversation;
- recording/transcript/summary linked to logistics case;
- compliance/audit event trail.

#### 7. Market / Country / Channel Routing

Nexus `Market`, `ChannelAccount`, country code routing, priority/fallback account, and OpenClaw routing should become Chatwoot fork routing extensions.

Useful for Switzerland and multi-country logistics rollout:

- CH/ZH/BE/Lausanne routing;
- market-specific SLA;
- market-specific policy bulletin;
- local language and timezone;
- provider account fallback.

#### 8. Admin Governance and Audit

Nexus `AdminAuditLog`, user capability overrides, and operational governance should be added to the fork because logistics customer support needs stricter compliance than generic live chat.

Use cases:

- who changed assignment;
- who changed customer-visible policy;
- who triggered work order;
- who sent a reply;
- who overrode AI recommendation;
- who accessed sensitive POD or phone data.

#### 9. Logistics-Specific Conversation Classifier

Port Nexus classification fields and AI intake into Chatwoot conversation metadata:

- tracking lookup;
- delivery reschedule;
- address issue;
- lost/damaged parcel;
- customs issue;
- COD/payment issue;
- complaint escalation;
- human handoff required.

### Tier 3 — Keep External Initially

These should not be embedded directly into Chatwoot at the first stage:

- full OpenClaw local runtime;
- Codex OAuth/token broker internals;
- Speedaf production credentials;
- raw POD evidence storage;
- low-level WebRTC/LiveKit provider runtime;
- high-risk background worker orchestration.

Keep these as internal services and call them from the Chatwoot fork through stable APIs.

## Recommended Fork Strategy

### Option A — Deep Fork Chatwoot Immediately

Pros:

- fastest path to visible product maturity;
- mature UI and workflow instantly available;
- less frontend rebuilding;
- easier to demo to business users.

Cons:

- Ruby/Rails/Vue stack becomes primary;
- future upstream merges need discipline;
- Nexus FastAPI services need adapter layer;
- internal AI/runtime/security modules must be carefully isolated.

Use this if the goal is: production customer-service product quickly.

### Option B — Chatwoot Shell + Nexus Services

This is the recommended first execution mode.

Chatwoot fork owns:

- inbox;
- conversation UI;
- contact UI;
- widget;
- agent operations;
- generic helpdesk behaviors.

Nexus services own:

- logistics case engine;
- AI/OpenClaw runtime;
- tracking facts;
- Speedaf work orders;
- WebCall runtime;
- governance/audit source of truth.

This reduces fork risk while still using Chatwoot as the product surface.

### Option C — Keep Nexus Primary, Chatwoot Sidecar

This was the original conservative report direction. It is still valid if the objective is maximum architectural independence, but it is less aggressive commercially.

Use only if:

- the team refuses Rails/Vue ownership;
- Chatwoot license/upstream strategy is unacceptable;
- Nexus must remain a fully custom product.

## Proposed Execution Plan

### Phase 0 — Fork Boundary and License Gate

Deliverables:

- confirm Chatwoot Community/MIT boundary;
- identify enterprise directories and avoid copying enterprise-only functionality;
- create internal fork strategy document;
- define upstream sync policy.

### Phase 1 — Read-Only Nexus Panel in Chatwoot

Goal: prove Chatwoot can display Nexus logistics context without changing core flow.

Implementation:

- add Chatwoot conversation side-panel extension;
- call Nexus profile endpoint by contact/conversation source ID;
- display active logistics cases and tracking facts;
- no writes to Nexus from Chatwoot yet.

Candidate Nexus endpoint:

- existing `/api/v1/integration/profile/{contact_id}` can be adapted.

### Phase 2 — Create Nexus Case from Chatwoot Conversation

Goal: agent can create a Nexus logistics case from a Chatwoot conversation.

Implementation:

- add "Create logistics case" action;
- call Nexus `/api/v1/integration/task`;
- pass contact ID, channel, summary, tracking number, priority, country/market, and metadata;
- write returned case reference into Chatwoot conversation custom attributes.

### Phase 3 — AI Logistics Reply Assist

Goal: Chatwoot agent can request Nexus/OpenClaw AI suggestions.

Implementation:

- add reply composer action: "Generate logistics reply";
- send conversation context to Nexus AI domain service;
- return suggested reply, required action, missing fields, confidence, safety flags;
- agent must review before sending.

### Phase 4 — Tracking Fact Auto-Panel

Goal: tracking facts show automatically when customer mentions a waybill.

Implementation:

- extract waybill from incoming messages;
- call Nexus tracking fact service;
- cache safe result on conversation custom attributes;
- show parcel status and next action in side panel;
- never expose raw PII beyond allowed fields.

### Phase 5 — Work Order / Speedaf Action

Goal: agent can trigger operational work from Chatwoot.

Implementation:

- add action buttons by issue type;
- create Speedaf work order through Nexus service;
- attach job ID and status to Chatwoot conversation custom attributes;
- write audit events.

### Phase 6 — WebCall and Voice Evidence

Goal: attach voice calls to Chatwoot conversation and Nexus case.

Implementation:

- use Nexus WebCall runtime;
- push voice session summary to Chatwoot conversation timeline;
- preserve recording/transcript/evidence in Nexus-controlled storage.

## Minimum Data Mapping for Chatwoot Fork

```text
Chatwoot account        -> Nexus tenant / organization
Chatwoot inbox          -> Nexus channel account / market route
Chatwoot contact        -> Nexus customer
Chatwoot contact_inbox  -> Nexus external contact link
Chatwoot conversation   -> Nexus conversation + optional logistics ticket
Chatwoot message        -> Nexus webchat message / ticket event / outbound message
Chatwoot custom attrs   -> lightweight mirror of Nexus logistics metadata
Nexus ticket            -> operational case source of truth
Nexus tracking fact     -> safe parcel status panel
Nexus AI intake         -> reply suggestion and action recommendation
```

## Product Positioning After Merge

This fork should not be marketed internally as "Chatwoot with small customizations".

Correct positioning:

```text
A logistics-native AI customer operations platform built on top of a mature open-source omnichannel support shell.
```

Or more directly:

```text
Chatwoot gives us the customer-service cockpit.
Nexus gives it logistics intelligence, AI execution, Speedaf workflow, and operational control.
```

## Final Recommendation

The corrected strategic recommendation is:

1. Keep the current Nexus repository as the domain-engine and experimentation base.
2. Use Chatwoot fork as the likely production agent-facing shell.
3. Merge Nexus strengths into Chatwoot in modules, starting with read-only logistics context, then task creation, then AI reply assist, then tracking fact automation, then work-order actions.
4. Keep high-risk AI/OpenClaw/Codex/runtime/Speedaf/POD components outside Chatwoot at first, exposed via stable internal APIs.
5. Do not immediately rewrite all Nexus into Ruby/Rails. Use Chatwoot as product shell, Nexus as domain-service layer, and only embed stable pieces once their contracts are proven.

This direction is more commercially aggressive and likely better for rapid business adoption than a pure Nexus-first rewrite.
