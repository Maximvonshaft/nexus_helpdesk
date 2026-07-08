# Nexus OSR Final Annual Development Plan

Baseline: `main` at `ec0af3fd8e853b447f47f901a69d036cae3d86e7` (`feat: add WebChat QA observability console`).

This document defines the final target architecture and full-year development plan for Nexus as a Speedaf multi-country customer service and operations runtime. It is intentionally not a minimum viable plan. It defines the best-practice end state and the full set of work needed to reach it without turning C-end customer conversations into long-term personal memory.

## 1. Final product doctrine

Nexus is not a private agent and not a customer long-term memory system.

Nexus is an operations-first customer service runtime:

- AI directly serves recipients on WebChat and WhatsApp.
- MCP is the primary truth source for shipment, ticket, and operational facts.
- Customer-visible knowledge is the answer source for service rules, policies, and supported commitments.
- Conversation history is audit context, not factual truth.
- Case Context is short-lived and tied to the current conversation, ticket, tracking number, or operational case.
- The system creates, routes, and follows up tickets/work orders when AI cannot close the issue immediately.
- Human handoff depends on human availability and escalation rules.
- Tool execution is governed by configuration, not hard-coded country or language logic.
- QA/debug/eval are first-class runtime capabilities.

The product category is:

`Nexus OSR = Nexus Operations Service Runtime`

## 2. Current-code feasibility assessment

The current repository already contains strong foundations:

| Current capability | Existing code area | End-state fit |
| --- | --- | --- |
| WebChat AI runtime | `backend/app/services/webchat_ai_service.py`, `webchat_runtime_ai_service.py` | Strong foundation |
| Runtime context guard | `backend/app/services/ai_runtime_context.py` | Strong foundation |
| Tracking fact object | `backend/app/services/tracking_fact_schema.py`, `tracking_fact_service.py` | Strong foundation, needs current/history split |
| Knowledge card model | `backend/app/models_control_plane.py` | Strong foundation |
| Customer-visible send boundary | `backend/app/services/customer_visible_message_service.py` | Strong foundation |
| Tool registry and tool policy seed | `backend/app/services/webchat_ai_decision_runtime/tool_registry.py` | Strong foundation, needs admin configuration |
| Policy gate | `backend/app/services/webchat_ai_decision_runtime/policy_gate.py` | Strong foundation, needs business policy config |
| Handoff service | `backend/app/services/webchat_handoff_service.py` | Strong foundation, needs human-hours policy |
| Derived support ledger | `backend/app/services/support_memory_ledger.py` | Useful foundation, not persistent Case Context |
| QA debug/eval models | `backend/app/models_webchat_debug.py` | Strong foundation, needs UI and CI gate |

Critical missing production modules:

- Persistent Case Context Ledger.
- Human Hours and Escalation Policy.
- Tool Execution Policy Admin.
- Controlled Action Executor.
- Auto Ticket Creation Flow.
- WhatsApp Group Routing.
- Runtime Decision Contract.
- Knowledge Quality Loop.
- QA Eval CI Gate.
- Operations Control Tower.

The final architecture is feasible because it extends existing runtime boundaries instead of replacing them.

## 3. Non-negotiable architecture principles

1. No long-term personal memory for C-end recipients.
2. Current case context is allowed; customer profile memory is not the default product behavior.
3. MCP facts override knowledge, case context, customer claims, and previous AI replies.
4. Customer claims are signals, not verified facts.
5. Previous AI replies are never facts.
6. Customer-visible AI responses must pass the customer-visible message boundary.
7. Tool execution must pass Tool Execution Policy and be auditable.
8. Country, language, routing, risk, and tool enablement must be configuration-driven.
9. Customer language is mirrored from the inbound message; no hard-coded language priority.
10. Country priority is not hard-coded; countries are separated by scope/configuration.
11. High-risk actions and promises are governed by configurable risk policy.
12. QA findings must become eval cases and eventually CI gates.

## 4. Final target architecture

```text
Inbound Channels
  WebChat / WhatsApp / Email
        ↓
Channel Gateway and Identity Resolver
        ↓
Conversation and Ticket Core
        ↓
Case Context Ledger
        ↓
Runtime Decision Engine
  - Intent
  - Language mirror
  - Risk policy
  - Human hours
  - Tool execution policy
  - Business reply type
        ↓
Evidence and Source Layer
  - MCP Truth Layer
  - Customer-visible Knowledge Base
  - Case Context
  - Customer Claim
  - Previous AI Reply = not evidence
        ↓
Action Runtime
  - Customer reply
  - Handoff
  - Auto ticket creation
  - MCP tool execution
  - Work order creation
  - WhatsApp group routing
        ↓
CustomerVisibleMessageService
        ↓
QA Debug / Eval / Continuous Improvement
```

## 5. Full module specification

### 5.1 Channel Gateway and Identity Resolver

Purpose: normalize WebChat, WhatsApp, and future email/session inputs into one runtime input contract.

Responsibilities:

- Resolve channel, tenant, country, market, queue, and conversation.
- Extract tracking reference when present.
- Resolve contact method.
- WebChat: collect email, WhatsApp, or phone before ticket creation when needed.
- WhatsApp: default to inbound WhatsApp account as contact method.
- Attach the channel identity to ticket/case context.

Deliverables:

- `ChannelRuntimeInput` schema.
- `channel_identity_resolver.py`.
- WebChat and WhatsApp adapters into the same runtime contract.
- Tests for anonymous WebChat, WebChat with email, WhatsApp inbound, and repeated visitor.

### 5.2 Conversation and Ticket Core

Purpose: every customer problem must be traceable to a conversation and, when action is required, a ticket.

Responsibilities:

- Create or reuse ticket.
- Bind conversation, tracking number, contact method, country, issue type, and source channel.
- Support customer follow-up by ticket number or tracking number.
- Preserve all transcripts for audit, not as long-term memory.

Deliverables:

- Ticket lookup by public ticket number, tracking hash/suffix, and conversation identity.
- Ticket reuse rules.
- Ticket state machine extension for AI-handled, waiting-customer, human-review, routed-to-agent, resolved.
- Tests for duplicate ticket prevention and customer follow-up lookup.

### 5.3 Case Context Ledger

Purpose: replace personal memory with short-lived operational context tied to current case.

Model: `case_contexts`.

Recommended fields:

- `id`
- `tenant_id`
- `conversation_id`
- `ticket_id`
- `channel`
- `country_code`
- `tracking_number_hash`
- `safe_tracking_reference`
- `contact_method_json`
- `issue_type`
- `customer_claim_summary`
- `last_mcp_fact_json`
- `missing_info_json`
- `handoff_requested`
- `ticket_created`
- `ai_actions_taken_json`
- `agent_handover_summary`
- `status`
- `expires_at`
- `closed_at`
- `created_at`
- `updated_at`

Rules:

- It is not customer long-term memory.
- It expires or closes with the ticket/conversation.
- It may be reused when the customer returns with the same ticket/tracking reference.
- It never overrides MCP facts.

Deliverables:

- `case_context_service.py`.
- `case_context_extractor.py`.
- `case_context_panel` API.
- Frontend panel in the agent workspace.
- Tests for extraction, update, close, reopen-by-ticket, and PII redaction.

### 5.4 MCP Truth Layer

Purpose: MCP is the primary operational truth source.

Required evidence roles:

- `mcp.tracking.current_status`
- `mcp.tracking.history_enrichment`
- `mcp.ticket.status`
- `mcp.work_order.created`
- `mcp.address_update.submitted`
- `mcp.cancel_request.submitted`

Required correction to current model:

- Split generic `fact_evidence_present` into current-status and enrichment roles.
- `speedaf.order.query` can satisfy current status.
- `speedaf.express.track.query` can enrich history but cannot satisfy current status alone.

Deliverables:

- `mcp_truth_contract.py`.
- `TrackingFactResult` extension.
- `TrackingTruthDecision` object.
- Tests for current status, history only, failed lookup, multiple candidate waybills, PII redaction.

### 5.5 Customer-visible Knowledge Base

Purpose: customer-visible business knowledge and service rules.

Current `KnowledgeItem` is retained and productized.

Additional metadata to standardize in `citation_metadata_json` or future columns:

- `allowed_commitments`
- `forbidden_claims`
- `related_tools`
- `customer_visible_template`
- `internal_notes`
- `golden_questions`
- `risk_policy_keys`

Runtime rules:

- Customer-visible knowledge can answer business/service questions.
- Knowledge cannot answer live shipment status.
- Knowledge can state commitments only when content explicitly supports them.
- Language follows inbound message; content can be translated by AI within source meaning.

Deliverables:

- Knowledge Card editor updates.
- Knowledge runtime source contract.
- Hit testing console.
- Golden question evaluation.
- Tests for country/channel/audience filtering and forbidden live tracking usage.

### 5.6 Runtime Decision Engine

Purpose: convert message + context + facts + knowledge + policies into a governed action.

Output contract: `RuntimeDecisionContract`.

Fields:

- `business_reply_type`
- `intent`
- `risk_level`
- `language`
- `required_information`
- `mcp_facts_used`
- `knowledge_sources_used`
- `case_context_used`
- `customer_claims_used`
- `tool_actions_requested`
- `handoff_decision`
- `ticket_decision`
- `routing_decision`
- `customer_reply_policy`
- `audit_reasons`

Business reply types:

- `tracking_status_answer`
- `knowledge_answer`
- `clarification`
- `handoff_notice`
- `ticket_created_notice`
- `tool_action_result`
- `complaint_escalation`
- `compensation_escalation`
- `no_answer`

Deliverables:

- `runtime_decision_contract.py`.
- `runtime_decision_engine.py`.
- Integration into WebChat AI runtime.
- Debug bundle extension.
- Tests for all business reply types.

### 5.7 Human Hours and Escalation Policy

Purpose: decide handoff vs offline ticket creation.

Model: `human_hours_policies`.

Fields:

- `country_code`
- `channel`
- `queue_key`
- `timezone`
- `working_hours_json`
- `holiday_calendar_json`
- `handoff_enabled`
- `offline_message_template`
- `auto_ticket_when_offline`
- `customer_wait_timeout_seconds`
- `fallback_action`
- `enabled`

Rules:

- Human online + handoff required -> request handoff.
- Human offline + handoff required -> tell customer human team is offline and create ticket.
- Customer cannot wait -> create ticket.
- Complaint/compensation -> can-do first, then escalate based on policy.

Deliverables:

- Admin policy console.
- Handoff integration.
- Offline ticket creation integration.
- Tests for timezone, holidays, online/offline, and customer wait timeout.

### 5.8 Escalation Risk Policy

Purpose: configurable high-risk customer service behavior.

Model: `escalation_policies`.

Fields:

- `risk_key`
- `country_code`
- `channel`
- `trigger_patterns_json`
- `semantic_intents_json`
- `max_ai_attempts`
- `handoff_required`
- `ticket_required`
- `forbidden_commitments_json`
- `allowed_resolution_actions_json`
- `enabled`

Default policy keys:

- `formal_complaint`
- `compensation`
- `refund`
- `legal_threat`
- `abusive_or_sensitive`
- `serious_service_failure`
- `personal_data_request`

Deliverables:

- Configurable risk policy service.
- Replacement/extension for regex-only behavior.
- Tests for complaint, compensation, refund, address update, customs promise, delivery guarantee.

### 5.9 Tool Execution Policy and Controlled Action Executor

Purpose: AI can execute open tools, but only through governed action runtime.

Model: `tool_execution_policies`.

Fields:

- `tool_name`
- `enabled`
- `ai_auto_executable`
- `risk_level`
- `requires_tracking_number`
- `requires_contact`
- `requires_customer_confirmation`
- `requires_human_confirmation`
- `allowed_channels_json`
- `allowed_countries_json`
- `customer_visible_success_template`
- `customer_visible_failure_template`
- `audit_level`

Executor responsibilities:

- Validate policy.
- Validate required context.
- Enforce idempotency.
- Execute tool or enqueue background job.
- Write tool call log.
- Update Case Context and Ticket.
- Produce customer-visible safe summary.

Deliverables:

- `controlled_action_executor.py`.
- `tool_execution_policy_service.py`.
- Execution handlers for `ticket.create`, `handoff.request.create`, `speedaf.workOrder.create`, selected MCP actions.
- Tests for enabled/disabled, confirmation, idempotency, failure, audit.

### 5.10 Auto Ticket Creation Flow

Purpose: create operational tickets from AI runtime when required.

Required inputs:

- `country_code`
- `channel`
- `conversation_id`
- `tracking_number` or safe reference when applicable
- `contact_method`
- `issue_type`
- `customer_description`
- `case_context_summary`
- `last_mcp_fact`
- `priority`

Rules:

- WebChat must collect contact method before ticket creation when missing.
- WhatsApp can use inbound account as default contact.
- Ticket creation must be idempotent by conversation + issue type + tracking hash.
- Ticket created notice goes through CustomerVisibleMessageService.

Deliverables:

- `auto_ticket_service.py`.
- Ticket creation tool handler.
- Contact collection UI card.
- Tests for WebChat contact collection, WhatsApp default contact, duplicate prevention, offline handoff.

### 5.11 WhatsApp Group Routing

Purpose: route operational tickets to country/agent groups.

Model: `whatsapp_routing_rules`.

Fields:

- `country_code`
- `issue_type`
- `channel`
- `destination_group_id`
- `fallback_group_id`
- `working_hours_key`
- `message_template`
- `enabled`

Flow:

- Ticket created or classified.
- Match country + issue type.
- Dispatch to destination group.
- If group unavailable, dispatch to fallback group.
- Record routing event on ticket.
- Later response/status updates sync back to ticket.

Deliverables:

- `whatsapp_routing_service.py`.
- Routing rules admin console.
- Group dispatch integration.
- Tests for routing, fallback, disabled rule, unknown country/issue.

### 5.12 QA Debug and Eval Gate

Purpose: make AI behavior visible and continuously testable.

Extend DebugRun with:

- `runtime_decision_contract_json`
- `case_context_snapshot_json`
- `human_hours_decision_json`
- `risk_policy_decision_json`
- `tool_execution_decision_json`
- `routing_decision_json`

Eval categories:

- Tracking without MCP fact must not answer live status.
- Tracking with MCP fact can answer status safely.
- Complaint/compensation escalates according to policy.
- Human offline creates ticket.
- WebChat contact missing asks for contact.
- WhatsApp contact defaults to account.
- Knowledge answer uses country/channel scoped item.
- Previous AI reply is not evidence.
- Customer claim is not verified fact.
- Tool disabled blocks execution.

Deliverables:

- Debug console UI.
- Eval case runner.
- CI workflow.
- Finding to eval case automation.

### 5.13 Operations Control Tower

Purpose: manage operations quality after runtime is connected.

Dashboards:

- Ticket volume by country/issue.
- AI auto-resolution rate.
- Handoff rate.
- Offline ticket creation rate.
- Tool execution success/failure.
- Routing success/failure.
- SLA risk.
- Knowledge hit quality.
- Top unresolved intents.
- QA finding trends.

Deliverables:

- `operations_control_tower_service.py`.
- Frontend dashboard.
- Export support.

## 6. Full-year delivery map

This is a full-year plan, but it is not a minimal staged roadmap. It is the complete delivery map for the final product.

### Month 1

- Runtime Decision Contract.
- Case Context schema/service.
- Human Hours policy schema/service.
- Escalation Risk policy schema/service.
- Tool Execution policy schema/service.
- Baseline tests and CI coverage.

### Month 2

- Controlled Action Executor.
- Auto Ticket Creation flow.
- WebChat contact collection.
- WhatsApp contact defaulting.
- Customer-visible ticket created notice.
- Case Context Panel backend.

### Month 3

- MCP Truth Contract split: current status vs history enrichment.
- Tracking truth enforcement in runtime decision.
- Tool action audit and idempotency.
- Handoff online/offline integration.
- Complaint/compensation policy integration.

### Month 4

- Knowledge Card productization.
- `allowed_commitments`, `forbidden_claims`, and `related_tools` metadata standard.
- Knowledge hit testing console.
- Runtime source attribution for backend debug.

### Month 5

- WhatsApp Group Routing rules.
- Group dispatch integration.
- Routing event audit.
- Fallback group handling.
- Ticket routing status sync.

### Month 6

- QA Debug Console v1 complete.
- DebugRun extension for Runtime Decision Contract.
- Finding to EvalCase automation.
- WebChat Evidence Eval CI.

### Month 7

- Operations ticket workspace.
- Agent handover panel.
- SLA-focused case queues.
- Country/issue filters.
- Agent action suggestions based on Case Context and MCP facts.

### Month 8

- Tool policy admin UI.
- Risk policy admin UI.
- Human hours admin UI.
- Routing rules admin UI.
- Permission and audit integration.

### Month 9

- Knowledge Quality Loop.
- Golden question sets by country.
- Unsupported question detection.
- Knowledge gap task creation.
- Knowledge correction workflow.

### Month 10

- Operations Control Tower.
- Country performance dashboards.
- AI auto-resolution and handoff analytics.
- Tool execution analytics.
- Routing success analytics.

### Month 11

- SOP SkillBank for internal operations.
- Skill trigger rules.
- Skill output schemas.
- Internal-only skill execution boundaries.
- Integration with tool execution policy.

### Month 12

- Production hardening.
- Load and latency optimization.
- Privacy/PII audit.
- Full regression suite.
- Runbook and rollback documentation.
- Final readiness review.

## 7. Performance design

Hot path must remain lean:

- Runtime context build: bounded retrieval and bounded recent context.
- Runtime decision validation: synchronous, deterministic, low-latency.
- MCP lookup: timeout-bounded and recorded.
- Customer-visible send: synchronous and auditable.

Async path:

- Case Context extraction enrichment.
- Eval case generation.
- Knowledge quality clustering.
- Operations analytics.
- Routing retries.
- Tool execution retries where safe.

Targets:

- Evidence/decision validation: under 20 ms.
- Case Context load: under 50 ms for normal ticket.
- Debug bundle generation: not on customer reply critical path unless already available.
- Tool execution: each tool has individual timeout and retry policy.

## 8. Test and acceptance strategy

Every module must ship with:

- Unit tests.
- Integration tests.
- Policy tests.
- Privacy/redaction tests.
- Regression eval cases for customer-visible behavior.

Global acceptance examples:

- Customer asks status without tracking number -> ask for tracking/contact.
- Customer asks status with MCP fact -> safe status answer.
- Customer asks status with only history enrichment -> no current-status claim.
- Customer claims delivered/not received -> customer claim only, not fact.
- Customer requests compensation -> can-do response then escalate according to policy.
- Human offline -> offline message + ticket creation.
- WhatsApp inbound -> contact defaults to WhatsApp account.
- WebChat no contact -> collect contact before ticket.
- Tool disabled -> do not execute.
- High-risk tool requiring confirmation -> require confirmation.
- Knowledge answer uses country/channel scoped item.
- Customer-visible reply contains no internal prompt/tool/MCP raw output.

## 9. Why this is the best-practice final plan

This plan is best for Nexus because it matches the actual product constraints:

- C-end recipients do not need long-term personal memory.
- MCP is the operational truth source.
- Knowledge is customer-visible business answer source.
- Operations tickets are the real closure mechanism.
- Countries and languages must be data-driven, not hard-coded.
- AI must be allowed to execute open tools, but only through auditable policy.
- Human availability and escalation policy are core product features, not edge cases.
- QA/debug/eval must be built into the runtime because customer-facing AI will fail without continuous correction.

The plan borrows selectively from the market:

- From Chatwoot: helpdesk, article/knowledge, handoff and team operations.
- From Dify: knowledge hit testing, retrieval quality, metadata filtering, evaluation discipline.
- From LangGraph: namespace, store, policy-oriented runtime separation.
- From OpenClaw: tool wiring, skill concepts, runtime boundaries.
- From CheetahClaws: memory hygiene and what-not-to-save discipline.
- From mem0: memory layer separation, but not personal long-term memory for recipients.

It deliberately avoids their weaknesses:

- No thread history as fact.
- No RAG hit as automatic truth.
- No MEMORY.md or prompt file bypassing customer-visible governance.
- No uncontrolled third-party skills.
- No automatic long-term user memory for C-end customers.
- No hard-coded country/language routing.

## 10. Final build directive

Build Nexus as an operations-first, evidence-governed runtime:

```text
Customer asks
  -> AI understands language and intent
  -> MCP provides facts
  -> Knowledge provides customer-visible policy
  -> Case Context carries current issue state
  -> Runtime Decision chooses reply/tool/ticket/handoff/routing
  -> CustomerVisibleMessageService sends only governed output
  -> QA/Eval records and improves the system
```

This is the final annual target architecture. Any implementation work that does not move the repository toward this architecture should be treated as secondary.