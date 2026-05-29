# v1.7.8 Template Block Adaptation and Performance Evidence

## Current conclusion

The previous PRs connected real WebChat, WebCall, and Email API capabilities, but that is not equivalent to full v1.7.8 template block migration.

## P0 performance decision

`livekit-client` must not be imported at top-level by any WebCall route or panel. It must be lazy loaded only when the visitor or operator starts an actual media action.

## Remaining template-block work

- WebChat: verify visual parity for queue, message stream, customer profile, AI suggestions, handoff, session actions.
- WebCall: top-level `/webcall` is connected to real queue/session, customer identity, AI turn, handoff, session action and timeline APIs. Follow-up PRs extended the WebChat thread contract with redacted runtime events, added auditable call-note writes, added `GET /api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/evidence` for redacted transcript segments, AI turns and AI action decisions, and added `POST/GET /api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/actions` for audited hold/resume/mute/keypad/transfer/add-participant commands. Remaining work is visual parity polish for live-console density and provider-side telephony execution behind those command rows.
- Email: verify visual parity for queue, thread, composer, draft/send/audit state. Existing and newly uploaded external ticket attachments are selectable from the Email composer and are bound to outbound draft/send records, SMTP MIME dispatch, and timeline payload readback. Provider status, retry/failure evidence, `runtime.manage` gated dead-message requeue, and outbound mailbox thread/message identity are now visible in the Email timeline. SMTP dispatch writes `Message-ID`, `In-Reply-To`, and `References` from the stored mailbox identity.
- Today Workbench: `/` now consumes `/api/lite/today-workbench` for the v1.7.8 Role Home blocks: role tasks, real metrics, SLA priority rows, interaction-state closure and command center actions. Remaining work is visual parity polish against the template screenshots and broader 33-screen registry migration.
- Control Tower / Governance: `/control-tower` now consumes `/api/lite/control-tower` for KPI/tower, manager action queue, team workload, channel health, bulletin impact, RBAC/governance lanes and template closure status. Manager actions can now create auditable `control_tower_action` operator tasks through `POST /api/lite/control-tower/actions`, and Provider Ops / Speedaf Wizard template blocks are backed by this governance write path plus their existing capability gates.
- QA / Training Loop: `/qa-training` now consumes `/api/lite/qa-training` for real WebCall/WebChat/Email/Ticket QA samples, scorecard, coaching tasks, knowledge gap loop and explicit template block closure status. Agent appeal writes `qa_appeal` operator tasks through `POST /api/lite/qa-training/appeals`; knowledge gaps write AI knowledge drafts plus `knowledge_gap` operator tasks through `POST /api/lite/qa-training/knowledge-gaps`. Both commands record TicketEvent/AdminAuditLog evidence.
- Knowledge Studio: `/knowledge-studio` now consumes `/api/lite/knowledge-studio` for real KnowledgeItem/KnowledgeChunk/KnowledgeItemVersion state, including asset library readiness, retrieval-test evidence, dedicated conflict-check, golden-test command, release lifecycle and explicit template closure status. Conflict scan calls `/api/knowledge-items/conflict-check`; golden tests call `/api/knowledge-items/golden-test` and assert expected source, expected answer, forbidden terms and minimum score against published retrieval evidence.
- AI Persona Builder: `/persona-builder` now consumes `/api/lite/persona-builder` for real PersonaProfile/PersonaProfileVersion state, including profile readiness, resolve-preview simulation, release lifecycle, publish/rollback linkage and runtime persona-context evidence. Submit-review, approve/reject, and release-window publish now write `persona_profile_reviews`; dedicated runtime evidence is implemented through `POST /api/persona-profiles/runtime-evidence`.

## Next PR

The next PR should continue template-block parity with remaining visible Operations/Engineering handoff screens, broader visual parity polish, or Persona runtime analytics.
