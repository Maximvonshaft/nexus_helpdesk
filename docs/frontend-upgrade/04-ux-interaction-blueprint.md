# 04 — UX Interaction Blueprint

## Status

Proposed. This document defines the target user experience before implementation begins.

## UX principle

NexusDesk should not expose internal technical complexity to frontline users. The interface should guide each role toward the next safe action.

Core principle:

> Show the operator what happened, why it matters, what evidence exists, what AI recommends, and what action is safe to take next.

## Target shell experience

The console should behave like an operations cockpit.

Target shell zones:

```text
Left rail        Primary modules and role-based navigation
Top bar          Global search, command palette, runtime status, user state
Main workspace   Current business workflow
Right panel      AI Copilot / context / selected entity intelligence
Bottom dock      Realtime event stream / sync status / warnings
```

## Global navigation model

Navigation should shift from a flat page list to an operations mental model.

Recommended IA:

```text
Operations
  Workspace
  WebChat
  Tickets / Queue

AI
  Copilot
  Governance
  Knowledge
  SOP
  Policy

Channels
  WebChat Channels
  WhatsApp / Telegram / Email readiness
  Channel Accounts

Control
  Runtime
  OpenClaw Bridge
  Safety Gate
  Jobs
  Audit Logs

Admin
  Users
  Teams
  Permissions
```

## Workspace target flow

The Workspace should support end-to-end ticket handling without unnecessary page switching.

Target flow:

```text
Open Workspace
→ see prioritized queue
→ select ticket
→ read customer conversation and context
→ inspect evidence / attachments / bulletins
→ review AI summary and suggested action
→ edit customer update / internal note
→ pass safety gate if replying
→ save / send
→ move to next ticket
```

## Workspace layout

```text
┌────────────────────────────────────────────────────────────┐
│ Queue filters / search / SLA / refresh                     │
├───────────────┬─────────────────────────┬──────────────────┤
│ Smart Queue   │ Ticket + Conversation   │ AI + Actions     │
│               │ Evidence + Bulletins    │ Safety + Notes   │
└───────────────┴─────────────────────────┴──────────────────┘
```

## Workspace interaction requirements

### Queue

Must provide:

- search by ticket, customer, tracking number
- filter by status, market, channel, priority
- future filter by SLA risk, unread state, assigned agent
- clear selected state
- keyboard navigation target in later phase

### Ticket detail

Must provide:

- ticket id/title
- customer identity
- channel and reply path
- tracking number
- status/priority/market
- last update time
- assigned user/team

### Conversation timeline

Must provide:

- role-separated messages
- timestamps
- channel labels where relevant
- message direction
- attachment indicators
- safe rendering through sanitized text

### Evidence

Must provide:

- system attachments
- OpenClaw attachment references
- POD / proof references in later phase
- missing-evidence warnings for factual claims

### AI Copilot

Must provide:

- customer intent
- issue summary
- missing information
- suggested next action
- draft reply
- confidence / evidence state
- explicit warning for unsupported logistics facts

### Action panel

Must provide:

- status update
- assignee update
- required action
- missing fields
- customer update
- resolution summary
- internal note
- dirty-state protection

## WebChat admin target flow

```text
Open WebChat
→ see active conversations
→ select conversation
→ review visitor context and thread
→ review AI suggestion / safety state
→ reply manually or hand off
→ conversation updates realtime or through fallback polling
```

WebChat admin should later include:

- channel configuration
- widget theme preview
- snippet generator
- allowed origin status
- runtime logs
- conversation replay

## WebChat visitor target flow

```text
Visitor opens customer site
→ clicks chat button
→ widget initializes or resumes conversation
→ visitor sends message
→ message appears immediately
→ agent reply appears in same widget
→ visitor can continue after reload
```

Visitor UX rules:

- no host page style pollution
- mobile viewport safe
- loading and failure status visible
- no internal ticket id exposed
- conversation token remains private to visitor browser state

## AI Governance target flow

```text
AI Operator opens Governance Studio
→ selects config domain: Persona / Knowledge / SOP / Policy
→ edits business form or JSON mode
→ validates schema
→ runs sandbox test
→ reviews diff
→ publishes version
→ rolls back if needed
```

UX requirements:

- business users should not be forced to edit raw JSON
- technical JSON mode remains available
- invalid config should be blocked before publish
- published vs draft state must be visually clear

## Runtime Control target flow

```text
Operator opens Runtime
→ sees API / worker / OpenClaw / job / safety gate health
→ sees event dock
→ investigates unresolved events
→ triggers safe replay/drop where permitted
```

Runtime UX requirements:

- separate normal state, warning state, and failure state
- show last successful sync/check time
- make dangerous actions visually distinct
- never hide degraded operation behind generic success labels

## Error-state requirements

Each major module must support:

- loading
- empty
- partial error
- full error
- unauthorized
- stale data
- offline/reconnecting where relevant

## Unsaved-change protection

Required in:

- Workspace ticket action form
- AI Governance draft editor
- WebChat configuration editor
- channel account configuration

Rules:

- warn before navigation or selection switch
- do not overwrite local edits with polling/realtime refresh
- show stale data indicator when refresh is paused due to local edits

## Accessibility requirements

Minimum rules:

- command palette accessible by keyboard
- dialogs/sheets must trap focus
- interactive cards must have clear focus state
- status badges must not rely only on color
- message timeline should use semantic grouping where practical
- realtime announcements should not spam screen readers

## Mobile / responsive requirements

Authenticated console:

- must remain usable on tablet width
- phone width may use stacked panels
- no default horizontal overflow

WebChat widget:

- must be fully usable on phone browsers
- should use dynamic viewport units where safe
- must not exceed viewport height
- input should remain reachable when keyboard opens where practical

## UX acceptance checklist

- Agent can complete one ticket without leaving Workspace.
- Agent can identify customer issue, evidence, AI suggestion, and next action within one screen model.
- WebChat visitor can send and receive messages after reload.
- AI operator can distinguish draft/published/version states.
- Runtime operator can distinguish healthy/degraded/failing states.
- Critical actions are reviewable before execution.
