# v1.7.8 Template Block Adaptation and Performance Evidence

## Current conclusion

The previous PRs connected real WebChat, WebCall, and Email API capabilities, but that is not equivalent to full v1.7.8 template block migration.

## P0 performance decision

`livekit-client` must not be imported at top-level by any WebCall route or panel. It must be lazy loaded only when the visitor or operator starts an actual media action.

## Remaining template-block work

- WebChat: verify visual parity for queue, message stream, customer profile, AI suggestions, handoff, session actions.
- WebCall: top-level `/webcall` is connected to real queue/session, customer identity, AI turn, handoff, session action and timeline APIs. This PR extends the WebChat thread contract with redacted runtime events so the audit panel is not frontend-only. Remaining work is visual parity polish for transcript/notes/live-console density.
- Email: verify visual parity for queue, thread, composer, draft/send/audit state. Existing and newly uploaded external ticket attachments are selectable from the Email composer and are bound to outbound draft/send records, SMTP MIME dispatch, and timeline payload readback. Provider status, retry/failure evidence, `runtime.manage` gated dead-message requeue, and outbound mailbox thread/message identity are now visible in the Email timeline. SMTP dispatch writes `Message-ID`, `In-Reply-To`, and `References` from the stored mailbox identity.
- Today Workbench: `/` now consumes `/api/lite/today-workbench` for the v1.7.8 Role Home blocks: role tasks, real metrics, SLA priority rows, interaction-state closure and command center actions. Remaining work is visual parity polish against the template screenshots and broader 33-screen registry migration.
- Control Tower / Governance: `/control-tower` now consumes `/api/lite/control-tower` for KPI/tower, manager action queue, team workload, channel health, bulletin impact, RBAC/governance lanes and template closure status. Remaining work is QA/Training Loop and other visible Operations/Engineering handoff template screens.
- QA / Training Loop: `/qa-training` now consumes `/api/lite/qa-training` for real WebCall/WebChat/Email/Ticket QA samples, scorecard, coaching tasks, knowledge gap loop and explicit template block closure status. Agent appeal remains read-model-only and is marked as missing a write endpoint.
- Knowledge Studio: `/knowledge-studio` now consumes `/api/lite/knowledge-studio` for real KnowledgeItem/KnowledgeChunk/KnowledgeItemVersion state, including asset library readiness, retrieval-test evidence, derived conflict rows, release lifecycle and explicit template closure status. Dedicated conflict-check and golden-test write/command endpoints remain missing and are marked as not implemented.
- AI Persona Builder: `/persona-builder` now consumes `/api/lite/persona-builder` for real PersonaProfile/PersonaProfileVersion state, including profile readiness, resolve-preview simulation, release lifecycle, publish/rollback linkage and runtime persona-context evidence. Submit-review, approve/reject, and release-window publish now write `persona_profile_reviews`; only a standalone runtime evidence query endpoint remains missing and is marked as not implemented.

## Next PR

The next PR should continue template-block parity with Knowledge Studio conflict/golden-test commands, Persona runtime evidence query, QA appeal writes, or remaining Operations governance write endpoints as the next likely cut after this Persona approval workflow lands.
