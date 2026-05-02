# 12 — Acceptance Criteria

## Status

Proposed. These criteria define the execution readiness gate and future implementation acceptance gate.

## Execution-readiness acceptance

The planning package is accepted only when reviewers agree that it clearly defines:

- current state
- product goals
- target architecture
- UX interaction model
- design system direction
- WebChat runtime direction
- AI governance direction
- security threat model
- test strategy
- execution epics
- engineering handoff

## Execution-readiness checklist

```text
[ ] README accepted
[ ] Current-state audit accepted
[ ] Product requirements accepted
[ ] Target architecture RFC accepted
[ ] UX interaction blueprint accepted
[ ] Design system blueprint accepted
[ ] WebChat runtime blueprint accepted
[ ] AI governance blueprint accepted
[ ] Security threat model accepted
[ ] Test strategy accepted
[ ] Execution epics accepted
[ ] Engineering handoff accepted
```

Implementation must not start until this checklist is accepted.

## Role-based acceptance

### Agent acceptance

The upgraded Workspace is successful when an agent can:

- see prioritized tickets
- open a ticket without losing context
- understand customer issue quickly
- see conversation history
- see evidence and attachments
- see relevant bulletins
- see AI summary and suggested action
- update workflow state
- avoid unsafe reply behavior
- move to the next ticket efficiently

### Supervisor acceptance

The upgraded console is successful when a supervisor can:

- understand queue health
- identify risky or blocked tickets
- understand agent-visible AI guidance
- review safety gate outcomes
- trust that AI and human actions are separated

### AI Operator acceptance

AI Governance Studio is successful when an AI operator can:

- edit persona, knowledge, SOP, and policy configs
- understand draft vs published state
- validate configs before publishing
- test sample customer messages
- review version diff
- publish safely
- rollback safely

### Channel Administrator acceptance

WebChat Control Center is successful when a channel administrator can:

- configure widget channel settings
- generate embed snippets
- preview widget theme
- verify allowed origin status
- review WebChat runtime logs
- preserve old snippet compatibility

### Operations Manager acceptance

Runtime Control Tower is successful when an operations manager can:

- see API health
- see worker/job health
- see OpenClaw status
- see unresolved events
- see safety-gate blocks
- distinguish healthy, degraded, and failing states

### WebChat Visitor acceptance

The WebChat widget is successful when a visitor can:

- open the widget quickly
- send a message
- receive a reply
- reload and continue the conversation
- use the widget on mobile
- never see internal ticket ids

## Technical acceptance

Each implementation phase must satisfy:

```text
[ ] Typecheck passes
[ ] Lint passes
[ ] Production build passes
[ ] Backend tests pass if backend touched
[ ] Targeted smoke tests pass
[ ] No critical browser console errors
[ ] API compatibility documented
[ ] Rollback plan documented
[ ] Screenshots attached for UI changes
```

## Security acceptance

```text
[ ] Public WebChat APIs do not expose internal ticket ids
[ ] Visitor token cannot access admin APIs
[ ] Admin APIs remain server-authorized
[ ] AI suggestions are visually distinct from verified facts
[ ] Safety gate blocks/reviews unsupported or sensitive content
[ ] Widget does not pollute host page styles
[ ] Realtime stream, if added, is authenticated and permission-filtered
[ ] Tokens and secrets are not logged
```

## Performance acceptance

```text
[ ] Console build size is measured
[ ] WebChat widget build size is measured
[ ] No unexplained route chunk growth
[ ] Long lists have virtualization plan
[ ] Polling additions are reviewed
[ ] Realtime fallback does not overload backend
```

## Release acceptance

```text
[ ] Feature branch used
[ ] PR reviewed
[ ] CI passed
[ ] Smoke checklist completed
[ ] Rollback plan available
[ ] Production behavior changes are feature-flagged when high risk
[ ] Post-deploy health checks defined
```

## Stop conditions

Implementation must stop and return to review if:

- login breaks
- Workspace ticket list/detail breaks
- WebChat old snippet breaks
- admin auth behavior changes unintentionally
- public API shape changes without review
- safety gate is bypassed
- AI can expose sensitive content
- rollback path is unclear
- build/typecheck fails

## Final acceptance statement

The frontend upgrade is accepted only when NexusDesk is measurably safer, more modular, more operator-friendly, and more commercially demonstrable without sacrificing current production stability.
