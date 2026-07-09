# Agent 3 — Tool Execution and Auto Ticket Production Path

Base branch: `feat/nexus-osr-runtime-foundation` (#451)

## Mission

Turn OSR tool proposals into governed production actions. The first production actions are `ticket.create` and `handoff.request.create`; then add a safe extension point for MCP/Speedaf work order actions without executing high-risk tools by default.

## Current facts to use

Existing code and #451 foundation:

- `backend/app/services/webchat_ai_decision_runtime/tool_registry.py`
- `backend/app/services/webchat_ai_decision_runtime/policy_gate.py`
- `backend/app/services/webchat_runtime_output_parser.py`
- `backend/app/services/nexus_osr/controlled_action_executor.py`
- `backend/app/services/nexus_osr/auto_ticket_service.py`
- `backend/app/services/nexus_osr/persistence.py`
- `backend/app/services/nexus_osr/runtime_bridge.py`
- `backend/app/tool_models.py`
- `backend/app/models.py`

## Scope

Implement production-grade execution path for OSR-controlled tool actions:

1. Convert existing AI runtime tool proposals into `RuntimeToolAction` objects.
2. Resolve `ToolExecutionPolicyRecord` by tool/country/channel.
3. Validate required context: tracking reference, contact method, customer confirmation, human confirmation.
4. Execute allowed tool handlers.
5. Write `ToolCallLog` or equivalent audit event.
6. Update `CaseContextRecord` and `RuntimeDecisionAuditRecord`.
7. Return a safe customer-visible action result template, but do not send it directly. Customer-visible send must stay under `CustomerVisibleMessageService`.

## Required first handlers

- `ticket.create`: use `create_or_reuse_ticket_from_case_context()`.
- `handoff.request.create`: use existing `request_webchat_handoff()`.
- `timeline.event.create`: create safe internal audit/timeline event only.

MCP/Speedaf write actions such as `speedaf.workOrder.create` may be scaffolded but must remain policy gated and disabled unless tests explicitly configure them.

## Hard rules

Do not:

- Execute high-risk write tools by default.
- Bypass `ToolExecutionPolicyRecord`.
- Bypass `CustomerVisibleMessageService`.
- Add provider-native tool execution.
- Store raw tracking numbers, raw phone numbers, raw addresses, or raw tool payloads in audit.
- Change `policy_gate.py` to loosen existing protections.

Do:

- Keep existing tool registry contracts compatible.
- Use idempotency keys.
- Add tests for blocked, missing context, confirmation required, allowed execution, duplicate ticket prevention.
- Make failures safe and auditable.

## Expected files likely touched

- `backend/app/services/nexus_osr/tool_execution_service.py`
- `backend/app/services/nexus_osr/controlled_action_executor.py`
- `backend/app/services/nexus_osr/auto_ticket_service.py`
- `backend/tests/test_nexus_osr_tool_execution_service.py`

Coordinate with Agent 1 and Agent 2 if touching `webchat_ai_service.py`. Prefer a pure service first.

## Acceptance tests

1. `ticket.create` blocked when policy disabled.
2. `ticket.create` blocked when tracking/contact required but missing.
3. `ticket.create` creates/reuses Ticket when policy allows.
4. `handoff.request.create` calls existing handoff service and suspends AI.
5. High-risk `speedaf.workOrder.create` blocked unless policy explicitly allows.
6. Confirmation-required tool returns confirmation_required without execution.
7. Tool action writes safe audit output with no raw PII/tracking.
8. Duplicate tool execution is idempotent.

## Prompt for the agent

You are Agent 3 for Nexus OSR. Your task is to implement the governed tool execution production path. Use #451 foundation, existing Tool Registry, Policy Gate, ToolExecutionPolicyRecord, ControlledActionExecutor, and auto_ticket_service. Do not invent new tool semantics. First make ticket.create and handoff.request.create production-ready. Keep high-risk Speedaf write actions disabled unless explicitly configured in tests. Ensure every action is policy-gated, idempotent, audited, and does not send customer-visible text directly.