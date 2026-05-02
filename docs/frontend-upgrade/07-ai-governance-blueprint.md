# 07 — AI Governance Blueprint

## Status

Proposed. This document defines the AI governance target before implementation begins.

## Current state

The current AI Control page already supports:

- config types: persona, knowledge, sop, policy
- scope types: global, market, team, channel, case_type
- draft content JSON
- create/update
- publish
- rollback
- published preview
- version history

This is a strong foundation, but the current UX is still closer to an engineering configuration page than an operator-friendly governance studio.

## Product goal

Upgrade AI Control into AI Governance Studio.

Target description:

> A governed interface where operators can define AI persona, knowledge, SOPs, and policy guardrails; test behavior before publishing; and safely roll back production AI behavior.

## Governance domains

### Persona

Defines:

- assistant identity
- tone
- language behavior
- escalation style
- brand constraints
- prohibited phrasing

### Knowledge

Defines:

- FAQs
- market rules
- channel rules
- customer-facing explanations
- logistics terminology
- bulletin-derived content

### SOP

Defines workflow rules by case type, for example:

- delayed parcel
- delivered not received
- customs issue
- address issue
- POD request
- damaged parcel
- lost parcel

### Policy

Defines hard and soft guardrails:

- allowed automatic replies
- replies requiring human review
- prohibited claims
- sensitive information restrictions
- logistics factual commitment rules
- channel-specific restrictions

## Target UX modules

```text
AI Governance Studio
  Persona Studio
  Knowledge Studio
  SOP Builder
  Policy Guardrail
  Sandbox Test
  Version Diff
  Rollback Center
```

## Editing modes

### Business form mode

For operators and supervisors.

Should provide structured fields such as:

- goal
- audience
- allowed behavior
- prohibited behavior
- escalation trigger
- reply examples
- evidence requirement

### Technical JSON mode

For engineering or advanced AI operators.

Should provide:

- schema validation
- formatting
- parse errors
- JSON path error messages
- eventual Monaco/CodeMirror editor

Both modes must edit the same underlying draft model.

## Draft / publish / rollback model

Rules:

- Draft config is editable.
- Published config is production-effective.
- Publishing creates immutable version history.
- Rollback creates a new published version based on an older snapshot.
- Draft and published states must be visually distinct.
- Unpublished draft changes must be visible.

## Version diff

Before publish, show:

- previous published version
- new draft version
- added rules
- removed rules
- changed rules
- scope change
- risk level

## Sandbox test

AI operators should be able to test with sample customer messages before publishing.

Sandbox input:

- customer message
- channel
- market
- case type
- optional tracking/evidence context

Sandbox output:

- AI summary
- customer intent
- suggested reply
- suggested internal action
- missing evidence
- safety gate decision
- policy references used

## Safety gate integration

AI Governance Studio must make safety behavior understandable.

Safety results:

```text
safe
needs_review
blocked
unsupported_fact
sensitive_content
policy_violation
```

Each result should explain:

- why it happened
- which policy was used
- whether override is allowed
- what evidence would unblock it

## Workspace integration

Workspace AI Copilot should consume published configs only.

It should display:

- summary
- intent
- next action
- suggested reply
- missing information
- safety status
- evidence requirement

AI suggestions must be visually separate from verified system facts.

## Audit requirements

AI config changes should be auditable:

- who edited
- who published
- when published
- version number
- notes
- diff summary
- rollback reason

## Security requirements

- Never expose system prompts to visitors.
- Never expose tokens, passwords, stack traces, or secret environment values.
- AI should not make logistics factual promises without evidence or human confirmation.
- Prompt injection from customer messages must not override policy.
- Published policies must be tenant/market/channel scoped where required.

## Target implementation phases

### Phase A — Governance UI foundation

- Split current AI Control page into feature modules.
- Keep current draft/publish/rollback behavior.
- Add clearer draft/published state UI.

### Phase B — Business form mode

- Add structured editor for persona/knowledge/SOP/policy.
- Keep JSON mode available.
- Add schema validation.

### Phase C — Diff and sandbox

- Add version diff.
- Add sandbox test UI.
- Show safety gate preview.

### Phase D — Workspace integration

- Show policy/safety references inside Workspace Copilot.
- Add evidence-aware reply suggestions.

## Acceptance criteria

- AI operator can create, edit, publish, and rollback configs.
- Draft and published states are visually distinct.
- Invalid JSON or invalid schema cannot be published.
- Sandbox can test a sample customer message before publishing.
- Workspace only consumes published configs.
- Safety gate decision is explainable to the operator.
- AI-generated content is visually distinct from verified facts.
