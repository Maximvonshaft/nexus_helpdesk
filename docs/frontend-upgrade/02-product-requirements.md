# 02 — Product Requirements

## Product intent

Upgrade NexusDesk from a production-shaped helpdesk console into an agent-native customer operations runtime.

This is not a cosmetic redesign. The product goal is to make the system clearly valuable to:

- frontline customer service agents
- supervisors
- operations managers
- AI operations owners
- channel administrators
- customer websites embedding WebChat
- technical operators maintaining OpenClaw / integration runtime

## Product position

NexusDesk should become the control surface for customer operations where humans, AI, channels, and safety rules work together.

Target positioning:

> A multi-channel, AI-assisted, safety-governed customer operations console for logistics support teams.

## Primary users

### Agent

A frontline support user who resolves customer conversations and tickets.

Needs:

- see the next most important ticket
- understand the customer issue quickly
- view conversation history and evidence
- use AI-generated summary and reply suggestions
- avoid unsafe or unsupported replies
- save workflow state without losing edits

### Supervisor

A team lead who manages queues, SLA risk, agent quality, and escalations.

Needs:

- monitor workload and aging tickets
- identify blocked cases
- review risky replies
- understand team performance
- enforce SOP and consistent customer messaging

### AI Operator

A user who manages AI rules, SOPs, knowledge, persona, and policies.

Needs:

- configure AI behavior without touching code
- publish and rollback AI configs
- test AI responses before publishing
- see guardrail behavior and safety-gate outcomes
- manage market/channel/team-specific behavior

### Channel Administrator

A user who configures channel accounts and embedded WebChat channels.

Needs:

- configure widget channels
- generate snippets
- define allowed origins
- configure theme and welcome behavior
- see channel runtime logs

### Operations Manager

A business/ops owner who checks whether the customer operation is healthy.

Needs:

- see queue health
- see runtime health
- see unresolved OpenClaw events
- understand outbound status and safety gate blocks
- understand where process failures occur

### WebChat Visitor

An end customer using the embedded widget.

Needs:

- open chat quickly
- submit a message reliably
- see responses in the same chat
- continue conversation after reload
- use mobile browser without layout failure

## Core product outcomes

1. Agents can resolve tickets from a single cockpit.
2. AI helps summarize, recommend actions, and draft replies but remains governed by policy.
3. WebChat becomes a configurable embeddable runtime, not just a static script.
4. Runtime status becomes visible and actionable.
5. Supervisors and operators can trust release, rollback, and audit boundaries.

## Required capabilities

### Workspace / Ticket Operations Cockpit

Must provide:

- smart queue list
- filters by status, market, channel, SLA risk, priority, unread state
- selected ticket detail
- conversation timeline
- customer context
- attachment/evidence panel
- market bulletin panel
- AI summary and suggested next action
- safety gate result before risky replies
- action panel for workflow updates
- dirty-state protection when switching tickets

### AI Copilot inside Workspace

Must provide:

- customer intent summary
- missing information extraction
- suggested reply draft
- suggested internal action
- confidence / evidence indicators
- warning when reply contains unsupported logistics facts
- clear human review requirement when needed

### WebChat Admin

Must provide:

- conversation list
- thread view
- reply composer
- safety-gate-aware reply submission
- channel configuration entry point
- widget snippet generator
- widget theme preview
- origin allowlist display/configuration in later implementation phase

### WebChat Widget

Must preserve:

- one-line script embed contract
- conversation persistence
- visitor token model
- mobile usability

Must add in target design:

- Shadow DOM isolation
- theme tokens
- multi-language configuration
- structured interaction cards
- real-time-ready transport
- graceful fallback polling
- no dependency on host website React or CSS

### AI Governance Studio

Must provide:

- persona management
- knowledge management
- SOP management
- policy guardrail management
- draft / publish / rollback model
- version diff
- sandbox test
- business form mode and technical JSON mode

### Runtime Control Tower

Must provide:

- API health
- worker/job health
- OpenClaw bridge status
- unresolved event visibility
- safety gate block visibility
- outbound status distinction between WebChat local ACK and external provider dispatch
- event stream / system activity dock

## Non-goals

These are explicitly out of scope for the execution-readiness phase:

- full rewrite
- replacing React/Vite with Next.js for the authenticated console
- changing existing public WebChat API behavior before compatibility plan is accepted
- changing production deployment topology
- changing database schema without a specific migration plan
- enabling external outbound dispatch without separate outbound governance review

## Success metrics

### Agent efficiency

- fewer clicks to understand a ticket
- fewer page switches per ticket
- shorter time to first safe response
- lower rate of incomplete internal notes

### Safety

- risky replies are visibly blocked or forced through review
- AI factual claims show evidence/review state
- sensitive system data is not exposed in replies or widget state

### WebChat quality

- one-line embed still works
- widget does not visually break host sites
- mobile widget works within viewport
- visitor conversation persists after reload

### Engineering quality

- route modules shrink through feature decomposition
- domain API clients are separated
- global CSS is reduced and tokenized
- E2E smoke covers core flows
- release and rollback plans exist before implementation

## Priority order

1. Current-state audit and architecture readiness
2. Frontend runtime foundation
3. Design system foundation
4. Workspace cockpit
5. WebChat runtime SDK
6. Realtime event runtime
7. AI Governance Studio
8. Runtime Control Tower hardening

## Product acceptance principle

A phase is accepted only when it improves the operator experience without breaking existing authenticated console flows, WebChat embed behavior, or OpenClaw/cloud connectivity assumptions.
