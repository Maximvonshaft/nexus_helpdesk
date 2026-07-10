# Agent 5 — OSR Admin, Debug, and Control Tower

Recovery work order: **#466**
Release candidate: **PR #456**
Reconstruction baseline: current `main` at the start of the work order.

## Scope

This workstream restores backend-only administration and observability for Nexus OSR without changing customer-visible reply behavior.

Implemented surfaces:

- `/api/admin/osr/human-hours-policies`
- `/api/admin/osr/escalation-policies`
- `/api/admin/osr/tool-execution-policies`
- `/api/admin/osr/whatsapp-routing-rules`
- `/api/admin/osr/policy-preview/*`
- `/api/admin/osr/runtime-decision-audits`
- `/api/admin/osr/case-contexts`
- `/api/admin/osr/debug-snapshot`
- `/api/admin/osr/control-tower/summary`
- Unified OSR snapshot in the existing WebChat AI debug bundle

## Authorization model

Every OSR Admin route performs both checks:

1. The authenticated user role must be exactly `admin`.
2. The user must pass the existing `runtime.manage` capability gate.

A non-admin user remains denied even if a capability override grants `runtime.manage`.

## Tenant model

`CaseContextRecord` and `RuntimeDecisionAuditRecord` are tenant-owned records. Their list, detail, update, debug, and Control Tower queries require the explicit `X-Nexus-Tenant` request header and always apply that tenant predicate in the database query.

Cross-tenant IDs return the same not-found response as missing IDs. Control Tower aggregations are tenant-filtered before counting or grouping. The WebChat debug bundle does not accept an operator-selected tenant: it takes the tenant directly from `WebchatConversation.tenant_key`.

The four policy tables do not currently contain `tenant_id`; changing that would require a prohibited data-model migration. They therefore remain explicitly marked `configuration_scope: global` and are protected by the strict Admin/runtime gate.

## Sensitive-data contract

Ordinary Admin, Debug, validation-error, and Control Tower responses must not expose:

- raw prompts or customer reply bodies;
- provider request/response payloads;
- tool arguments, tool results, or raw tool payloads;
- raw tracking numbers;
- phone numbers or email addresses;
- credentials, tokens, secrets, or API keys;
- raw provider WhatsApp group IDs.

WhatsApp routing responses use one serializer across list, detail, create, update, preview, and delete. Provider group references are represented as:

- a presence boolean;
- a deterministic SHA-256 prefix;
- a stable generated key such as `provider-group:<hash-prefix>`.

No raw provider-group detail endpoint is retained.

FastAPI validation errors on this router omit the Pydantic `input` and `ctx` fields. Database write failures are mapped to generic conflict or write-failure codes rather than returning database exception text.

## RuntimeDecisionAudit

`RuntimeDecisionAuditRecord` is strictly read-only. Only `GET` list and detail routes exist. The serializer emits a safe decision summary and never returns `decision_json` or raw case-context payloads.

## Case Context updates

The update schema permits only:

- `status`, restricted to `CaseContextStatus`;
- `issue_type`, restricted to a safe configuration key;
- `routed_group_key`, restricted to a safe configuration key;
- `handoff_requested`;
- `agent_handover_summary`, redacted before persistence;
- `missing_info_json`, restricted to safe configuration keys.

Tracking hashes, contact methods, customer claims, MCP facts, AI actions, expiry, closure, ticket flags, tenant identity, and record identifiers cannot be changed through the Admin API.

## Unified WebChat debug snapshot

The WebChat debug bundle composes a single tenant-scoped OSR snapshot containing:

- `mode`;
- `reply_metadata_audit`;
- `latest_runtime_audit`;
- `case_context_snapshot`;
- `policy_snapshot`;
- `tool_execution_summary`;
- `operations_dispatch_summary`;
- `evidence_sources`;
- `missing_evidence`.

The existing debug bundle sanitizer was hardened for credentials, provider payloads, provider group IDs, tool arguments/results, tracking references, phone numbers, and email addresses.

## Non-goals and unchanged behavior

This workstream does not modify:

- ticket numbering or auto-ticket transactions;
- escalation prefilter behavior;
- Case Context lifecycle or migrations;
- Dispatch Outbox;
- tracking or knowledge runtime;
- WhatsApp sidecar;
- customer-visible message generation or sending;
- deployment, tags, or release readiness state.

## Focused acceptance coverage

`backend/tests/test_nexus_osr_admin_api.py` covers:

- strict Admin authorization;
- global policy CRUD and validation;
- consistent provider-group redaction across all response surfaces;
- hostile validation payload non-echo;
- tenant isolation for list/detail/update/debug/Control Tower;
- strictly read-only RuntimeDecisionAudit routes;
- Case Context safe-field mutation and pre-persistence redaction;
- unified OSR debug snapshot shape and redaction;
- router mount and WebChat composition source contracts.
