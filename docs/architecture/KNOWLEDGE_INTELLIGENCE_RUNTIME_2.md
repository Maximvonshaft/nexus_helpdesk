# Knowledge Intelligence Runtime 2.0

Status: PR382 architecture baseline  
Scope: documentation and evaluation specification only  
Production impact: none

## 1. Decision

NexusDesk should evolve from a generic RAG-backed helpdesk into a logistics-native Knowledge Intelligence Runtime, hereafter **KIR 2.0**.

PR381 closed the WebChat Fast fallback defect: when trusted KB direct_answer evidence is already available, the final WebChat Fast response must not fall back to server_safe_fallback or create unwanted handoff side effects. That work is complete and production-proven.

PR382 starts the next layer: making the knowledge runtime understand logistics operations, especially last-mile delivery scenarios such as recipient absent, failed delivery attempt, redelivery, address issue, refusal, cancellation, complaint escalation, POD dispute, and live tracking boundaries.

This PR does **not** change runtime behavior. It defines the target architecture, current gaps, delivery plan, and golden-case contract for PR383-PR386.

## 2. Current runtime shape

The current system is a rules-augmented hybrid RAG and direct-answer framework:

```text
Customer query
  -> build_webchat_runtime_context
  -> retrieve_published_chunks / knowledge_runtime_v2
  -> knowledge_context / locked_facts / evidence_pack
  -> WebChat AI decision runtime
  -> provider_runtime / codex_app_server
  -> policy gate / tool executor
  -> WebChat Fast response
```

Main current components:

- `backend/app/models_control_plane.py`
  - `KnowledgeItem`
  - `KnowledgeItemVersion`
  - `KnowledgeChunk`
- `backend/app/services/knowledge_service.py`
  - create / publish lifecycle
- `backend/app/services/knowledge_retrieval_service.py`
  - query analysis
  - candidate retrieval
  - scoring
  - direct_answer scoring threshold
- `backend/app/services/knowledge_runtime_v2/`
  - runtime v2 retrieval path
- `backend/app/services/knowledge_grounding_service.py`
  - trusted direct_answer selector
- `backend/app/services/ai_runtime_context.py`
  - persona context
  - knowledge context
  - locked facts
  - evidence pack
- `backend/app/services/webchat_ai_decision_runtime/`
  - AI decision schema
  - policy gate
  - tool execution
  - audit
- `backend/app/services/provider_runtime/`
  - provider dispatch and output contract
- `backend/app/api/webchat_fast.py`
  - customer-facing fast reply API

## 3. Target architecture

KIR 2.0 target flow:

```text
Customer Query
  -> Channel Normalizer
  -> Logistics Intent & Entity Parser
  -> Query Rewrite / Expansion
  -> Hybrid Candidate Retrieval
       - lexical candidate
       - dense vector candidate
       - structured fact candidate
       - exact alias candidate
       - ontology candidate
       - tool/context candidate
  -> Deterministic Reranker
       - exact fact_question match
       - exact alias match
       - domain intent match
       - metadata compatibility
       - answer_mode suitability
       - wrong-domain penalty
       - conflict penalty
  -> Grounding Decision
       - direct_answer
       - guided_answer
       - tool_call
       - clarification
       - handoff
  -> Answer Planner
  -> Policy Gate
  -> Tool Executor
  -> Evidence-grounded Response
  -> Trace / Evaluation / Feedback Loop
```

## 4. Core design principles

### 4.1 Logistics-first, not FAQ-first

Logistics customer support is not only document Q&A. It is event-driven operations.

KIR 2.0 must understand operational events such as:

- `tracking_status`
- `delivery_attempt_failed`
- `recipient_absent`
- `redelivery`
- `address_issue`
- `address_change`
- `refusal`
- `cancellation`
- `complaint_escalation`
- `lost_or_damaged`
- `proof_of_delivery`
- `driver_contact`
- `pickup_issue`
- `customs_issue`
- `sla_delay`
- `handoff_request`

### 4.2 Knowledge and tools are separate evidence classes

Knowledge can answer policy questions. It must not invent live shipment status.

Examples:

- Policy question: “What happens if I missed delivery?” -> KB policy / direct_answer.
- Live status question: “Where is my parcel now?” -> tracking fact tool.
- Action request: “Change my address.” -> verification + Speedaf tool or handoff.
- Escalation: “I want to complain.” -> work order / handoff boundary.

### 4.3 Direct answer is authoritative only when selected correctly

`answer_mode=direct_answer` is not enough. Selection must prove:

- item is active;
- fact is approved;
- citation/source metadata exists;
- channel/audience/language match;
- domain intent matches the customer query;
- wrong-domain KB has been excluded;
- answer mode is appropriate for the requested operation.

### 4.4 Wrong-KB prevention is mandatory

The last-mile failure showed that unrelated KB can compete with domain policy. KIR 2.0 must prevent examples such as:

- last-mile missed delivery question answered by fee/pricing KB;
- tracking live-status question answered by generic policy KB;
- address-change action answered only with policy text;
- refusal/cancellation treated as a generic FAQ answer;
- complaint escalation swallowed by direct_answer.

### 4.5 Trace is a product requirement

Every production AI answer must be explainable through trace data:

- original user message;
- normalized query;
- detected logistics intent;
- extracted entities;
- query rewrite terms;
- retrieval candidates;
- reranker scores;
- selected KB/tool/action;
- policy gate decision;
- tool execution records;
- final answer contract;
- fallback or handoff reason, if any.

## 5. Required modules for KIR 2.0

### 5.1 Logistics intent taxonomy

Proposed new module:

```text
backend/app/services/logistics_intent_taxonomy.py
```

Responsibility:

- define logistics intents;
- define multilingual aliases;
- define operation boundaries;
- define intent-to-answer-mode compatibility;
- define intent-to-tool compatibility.

### 5.2 Logistics query understanding

Proposed new module:

```text
backend/app/services/logistics_query_understanding.py
```

Responsibility:

- detect logistics intent;
- extract entities;
- rewrite natural customer phrasing into operational terms;
- output structured trace.

Example:

```json
{
  "raw_query": "The courier came but I was not home. Will you deliver again?",
  "primary_intent": "delivery_attempt_failed.recipient_absent",
  "secondary_intents": ["redelivery"],
  "entities": {"delivery_attempt": true, "recipient_absent": true},
  "expanded_terms": [
    "recipient not at home",
    "missed delivery attempt",
    "failed delivery attempt",
    "second delivery attempt",
    "next working day redelivery",
    "order verification"
  ]
}
```

### 5.3 Hybrid candidate retrieval

Proposed extension to:

```text
backend/app/services/knowledge_retrieval_service.py
backend/app/services/knowledge_runtime_v2/
```

Candidate sources:

- lexical candidate;
- PostgreSQL full-text candidate;
- dense vector candidate if embeddings are ready;
- direct answer structured fact candidate;
- exact alias candidate;
- ontology candidate;
- metadata-filtered candidate.

### 5.4 Deterministic reranker

Proposed new module:

```text
backend/app/services/knowledge_reranker.py
```

Initial scoring contract:

```text
exact_alias_match            +100
fact_question_match           +90
intent_match                  +70
same_domain                   +50
direct_answer                 +40
approved_fact                 +30
metadata_channel_match        +10
metadata_audience_match       +10
wrong_domain_penalty         -100
operation_boundary_penalty   -150
conflicting_fact_penalty     -200
```

This should be deterministic first. LLM reranking can be added later, but production must have deterministic explainability.

### 5.5 Knowledge domain guard

Proposed new module:

```text
backend/app/services/knowledge_domain_guard.py
```

Responsibility:

- prevent wrong-KB selection;
- enforce live status boundary;
- enforce action boundary;
- block unrelated direct_answer when domain mismatch is severe.

### 5.6 Answer planner

Proposed new module:

```text
backend/app/services/answer_planner.py
```

Responsibility:

```text
intent + evidence + policy -> response plan
```

Plan types:

- `direct_answer`
- `guided_answer`
- `tool_call_tracking`
- `tool_call_address_update`
- `tool_call_refusal`
- `tool_call_cancel`
- `work_order_create`
- `clarify`
- `handoff`

### 5.7 Evaluation and simulation

Required artifacts:

```text
backend/tests/fixtures/logistics_knowledge_golden_cases.spec.json
backend/scripts/run_logistics_knowledge_eval.py
.github/workflows/logistics-knowledge-runtime-eval.yml
```

The evaluation must cover:

- retrieval correctness;
- domain classification;
- reranking correctness;
- wrong-KB prevention;
- answer mode correctness;
- tool/handoff boundary correctness;
- no unwanted side effects;
- public WebChat compatibility.

## 6. Rollout strategy

### PR382

Audit and architecture only. No runtime code change.

### PR383

Introduce logistics taxonomy and query understanding. Run in shadow mode. No production behavior change unless explicitly enabled.

### PR384

Introduce candidate fusion, deterministic reranker, wrong-KB prevention, and retrieval evaluation gates.

### PR385

Introduce answer planner and formal tool/action boundaries.

### PR386

Introduce evaluation workflow, trace artifacts, dashboard-facing trace contracts, and production rollout gates.

## 7. Non-goals

KIR 2.0 must not:

- hardcode last-mile answers in API handlers;
- bypass retrieval;
- collapse all logistics questions into one KB answer;
- answer live tracking status from policy documents;
- execute address/refusal/cancel operations without verification/tool boundary;
- remove PR381 protections;
- weaken origin, tenant, or channel constraints;
- remove audit/evidence requirements.

## 8. Definition of done

KIR 2.0 is complete only when:

- 100+ logistics golden cases pass;
- precise and natural last-mile queries both pass;
- wrong-KB competition cases pass;
- low-signal negative controls pass;
- tracking boundary cases pass;
- tool/handoff boundary cases pass;
- WebChat Fast production-like sidecar probes pass;
- public demo smoke passes where edge/WAF allows;
- traces explain every retrieval/rerank/plan decision;
- rollback path is documented;
- no production behavior is shipped without explicit gate and evidence.
