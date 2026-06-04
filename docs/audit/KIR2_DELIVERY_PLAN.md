# KIR 2.0 Delivery Plan

Status: PR382 delivery baseline  
Runtime behavior change in this PR: none

## 1. Epic objective

Epic: **Knowledge Intelligence Runtime 2.0**

Objective: upgrade NexusDesk from generic RAG-backed WebChat AI into a logistics-native customer operations intelligence runtime.

The epic is complete only when the system can reliably decide whether to:

- answer from approved KB;
- ask a clarification;
- retrieve tracking facts;
- execute or prepare an operation tool;
- create a work order;
- hand off to a human;
- refuse unsupported/unverified operations.

## 2. Delivery principles

1. No hardcoded last-mile answers.
2. No retrieval bypass.
3. No production runtime change without tests and rollout gate.
4. Every runtime answer must be explainable through trace.
5. Logistics domain intent must be first-class.
6. KB, tools, and handoff are separate evidence/action classes.
7. Side effects must be testable: no unwanted ticket, handoff, or tool call.
8. Production rollout must preserve rollback container/image discipline from PR381.

## 3. PR plan

## PR382: Audit and architecture

Status: this PR.

Scope:

- document current-state architecture;
- document target KIR 2.0 architecture;
- document gap matrix;
- define delivery plan;
- define logistics golden-case spec.

Files:

- `docs/architecture/KNOWLEDGE_INTELLIGENCE_RUNTIME_2.md`
- `docs/audit/KIR2_CURRENT_STATE_AUDIT.md`
- `docs/audit/KIR2_GAP_MATRIX.md`
- `docs/audit/KIR2_DELIVERY_PLAN.md`
- `backend/tests/fixtures/logistics_knowledge_golden_cases.spec.json`

Acceptance:

- docs are internally consistent;
- golden-case spec covers at least 100 logistics scenarios;
- no runtime code changed;
- no production deployment required.

## PR383: Logistics intent taxonomy and query rewrite

Scope:

- add logistics intent taxonomy;
- add query understanding module;
- normalize natural customer phrasing into operational intent;
- run in shadow mode;
- add tests for intent extraction and query rewrite.

Proposed files:

- `backend/app/services/logistics_intent_taxonomy.py`
- `backend/app/services/logistics_query_understanding.py`
- `backend/tests/test_logistics_query_understanding.py`
- `backend/tests/fixtures/logistics_intent_cases.json`

Required intents:

- `tracking_status`
- `delivery_attempt_failed`
- `recipient_absent`
- `redelivery`
- `address_issue`
- `address_change`
- `refusal`
- `cancellation`
- `complaint_escalation`
- `proof_of_delivery`
- `driver_contact`
- `pickup_issue`
- `customs_issue`
- `sla_delay`
- `handoff_request`

Acceptance:

- precise and natural last-mile wording maps to `delivery_attempt_failed.recipient_absent` and `redelivery`;
- address-change wording maps to action intent, not direct_answer only;
- tracking wording maps to tracking tool boundary;
- hello and generic greetings do not map to business intents;
- no production behavior change unless a shadow trace flag is enabled.

## PR384: Hybrid retrieval, reranker, and wrong-KB prevention

Scope:

- introduce candidate source fusion;
- introduce deterministic reranker;
- introduce wrong-KB domain guard;
- integrate logistics query understanding with retrieval;
- add wrong-KB competition tests.

Proposed files:

- `backend/app/services/knowledge_candidate_fusion.py`
- `backend/app/services/knowledge_reranker.py`
- `backend/app/services/knowledge_domain_guard.py`
- updates to `backend/app/services/knowledge_retrieval_service.py`
- updates to `backend/app/services/knowledge_grounding_service.py`
- `backend/tests/test_knowledge_logistics_retrieval.py`
- `backend/tests/test_knowledge_wrong_kb_prevention.py`

Candidate sources:

- lexical candidate;
- full-text candidate;
- dense/vector candidate if available;
- structured direct-answer candidate;
- exact alias candidate;
- ontology candidate;
- metadata candidate.

Reranker minimum contract:

- exact alias match outranks generic keyword hit;
- fact_question match outranks answer-body incidental match;
- domain intent match outranks unrelated same-city match;
- wrong-domain penalty can disqualify direct_answer;
- action requests cannot be answered by policy-only KB when a tool boundary is required.

Acceptance:

- last-mile precise query retrieves correct direct_answer;
- last-mile natural query retrieves correct direct_answer;
- Rümlang fee KB cannot outrank last-mile policy KB;
- live tracking status query cannot be answered by generic policy KB;
- low-signal hello does not ground to logistics KB.

## PR385: Answer planner and tool boundary

Scope:

- introduce first-class answer planner;
- map intent + evidence to response plan;
- formalize tool/handoff/clarification boundaries;
- integrate planner with WebChat Fast AI runtime.

Proposed files:

- `backend/app/services/answer_planner.py`
- `backend/app/services/logistics_action_boundary.py`
- updates to `backend/app/services/webchat_ai_decision_runtime/`
- updates to `backend/app/api/webchat_fast.py`
- `backend/tests/test_answer_planner.py`
- `backend/tests/test_webchat_fast_logistics_action_boundaries.py`

Plan types:

- `direct_answer`
- `guided_answer`
- `tracking_tool`
- `address_change_tool`
- `refusal_tool`
- `cancel_tool`
- `work_order_create`
- `clarify`
- `handoff`

Acceptance:

- missed-delivery policy question -> direct_answer;
- where-is-my-parcel -> tracking tool / tracking fact boundary;
- change address -> verification/tool boundary;
- refusal -> refusal tool or handoff boundary;
- cancellation -> cancel tool or handoff boundary;
- complaint -> work order or handoff;
- no unwanted side effects.

## PR386: Evaluation, observability, and rollout gates

Scope:

- add logistics knowledge evaluation runner;
- add CI workflow;
- add trace contract;
- add production-like sidecar probe;
- add rollout and rollback documentation.

Proposed files:

- `backend/scripts/run_logistics_knowledge_eval.py`
- `.github/workflows/logistics-knowledge-runtime-eval.yml`
- `docs/ops/KIR2_ROLLOUT_RUNBOOK.md`
- `docs/ops/KIR2_ROLLBACK_RUNBOOK.md`
- `docs/observability/KIR2_TRACE_CONTRACT.md`

Acceptance:

- 100+ golden cases pass;
- evaluation output includes per-case retrieval, reranker, planner, answer-mode, tool-boundary, side-effect status;
- CI blocks regression;
- sidecar probe can validate production-like WebChat Fast without modifying production data;
- rollout runbook includes rollback image/container strategy.

## 4. Runtime rollout plan

### Stage 1: Shadow mode

KIR 2.0 intent and reranker run but do not affect customer response. Trace is recorded for comparison.

### Stage 2: canary mode

Enable KIR 2.0 for a controlled subset of WebChat Fast sessions.

### Stage 3: domain-limited production

Enable for selected intents:

- last-mile policy;
- SLA policy;
- address issue policy.

Do not yet enable for high-risk action flows without tool-boundary proof.

### Stage 4: full WebChat production

Enable once golden cases, sidecar probe, and public smoke pass.

## 5. Rollback strategy

Rollback options:

1. Disable KIR 2.0 feature flag and fall back to PR381 runtime.
2. Revert PR-specific feature branch before merge if CI fails.
3. Keep prior production image/container for at least 24 hours after runtime rollout.
4. Preserve `server_safe_fallback` protections from PR381 even during rollback.

## 6. Required evidence per PR

Every PR must include:

- changed-file summary;
- exact tests run;
- expected outputs;
- negative controls;
- no-production-change statement if applicable;
- rollback statement;
- remaining risks.

## 7. Completion definition for the epic

KIR 2.0 is not complete until:

- PR382-PR386 are merged;
- logistics golden cases pass;
- production sidecar probe passes;
- public WebChat smoke passes;
- trace contract is available;
- production rollout runbook is approved;
- rollback plan is verified;
- at least last-mile, tracking, address change, refusal, cancellation, complaint, and SLA delay scenarios have positive and negative tests.
