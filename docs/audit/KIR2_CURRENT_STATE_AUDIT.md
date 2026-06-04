# KIR 2.0 Current State Audit

Status: PR382 factual audit baseline  
Runtime behavior change: none

## 1. Executive assessment

Current NexusDesk knowledge runtime is a rules-augmented hybrid RAG and structured direct-answer framework. It is strong enough to support approved business facts, direct_answer replies, runtime context, and evidence traces. PR381 proved that trusted KB direct_answer evidence can reach WebChat Fast production without falling back to server_safe_fallback.

The current gap is not the absence of RAG. The gap is logistics-domain intelligence:

- weak last-mile intent taxonomy;
- limited query rewrite for natural customer language;
- insufficient wrong-KB prevention;
- no formal deterministic reranker layer;
- no answer planner that formally separates KB answer, tool call, clarification, and handoff;
- evaluation is not yet logistics-scenario complete.

## 2. Audited source areas

### 2.1 Data model

Primary files:

- `backend/app/models_control_plane.py`
- `backend/alembic/versions/20260525_0033_knowledge_chunks_runtime_context.py`
- `backend/alembic/versions/20260526_0034_knowledge_rag_business_facts.py`
- `backend/alembic/versions/20260601_0046_knowledge_runtime_v2.py`
- `backend/alembic/versions/20260601_0047_knowledge_runtime_pg_hybrid.py`

Main concepts:

- `KnowledgeItem`
- `KnowledgeItemVersion`
- `KnowledgeChunk`
- `fact_question`
- `fact_answer`
- `fact_aliases_json`
- `fact_status`
- `answer_mode`
- `citation_metadata_json`
- `published_version`
- `indexed_version`

Assessment:

- Versioned and structured enough for controlled facts.
- Supports direct_answer and approved business facts.
- Still lacks explicit domain/intent fields such as `logistics_intent`, `operation_boundary`, `answer_plan_type`, and `conflict_group`.

### 2.2 Knowledge authoring / publishing / indexing

Primary files:

- `backend/app/services/knowledge_service.py`
- `backend/app/services/knowledge_document_service.py`
- `backend/app/services/knowledge_retrieval_service.py`

Current strengths:

- create/publish/index flow exists;
- structured business facts become chunks;
- `fact_question`, `fact_answer`, `fact_aliases`, and citation metadata are indexed into chunk metadata;
- chunking uses bounded text lengths.

Current weakness:

- publishing does not require a domain intent classification;
- alias quality is left to authoring discipline;
- no conflict group validation;
- no wrong-domain simulation at publish time.

### 2.3 Retrieval

Primary files:

- `backend/app/services/knowledge_retrieval_service.py`
- `backend/app/services/knowledge_runtime_v2/`

Current behavior:

- analyzes query into normalized terms, service terms, business terms, country terms, intent terms, numeric terms, and high-value terms;
- pulls candidate rows from active published chunks and active knowledge items;
- scores candidates using exact phrase, structured fact, direct_answer, business fact, title, question/alias, answer, keyword, entity, numeric, coverage, metadata, and priority signals;
- returns `KnowledgeRetrievalResult` with hits, total, query analysis, candidate count, top hits, grounding source, trace, retrieval methods, no answer reason, and latency.

Current strengths:

- deterministic and explainable;
- structured direct_answer receives scoring boosts;
- metadata filters exist for channel, audience, language, market;
- runtime v2 hook exists.

Current weakness:

- logistics intent coverage is shallow;
- no dedicated domain ontology for shipment lifecycle;
- no strong phrase-level alias candidate source before broad SQL terms;
- no separate reranker stage;
- no hard wrong-domain penalty;
- natural last-mile customer language can miss retrieval or be outranked by unrelated KB.

Observed production failure class:

- last-mile recipient-absent / missed-delivery questions did not reliably retrieve the intended KB;
- unrelated demo fee KB could compete with last-mile delivery policy KB;
- this is a retrieval/selector failure, not a server fallback failure.

### 2.4 Grounding selector

Primary file:

- `backend/app/services/knowledge_grounding_service.py`

Current role:

- selects trusted direct_answer evidence;
- participates in PR381 final API guard / pre-provider locked fact paths;
- prevents certain explicit action/handoff cases from being swallowed by direct_answer.

Current weakness:

- selector relies on retrieval candidates being correct;
- selector does not yet enforce a strong domain intent match;
- selector does not yet apply explicit wrong-KB penalties such as fee KB vs last-mile policy query.

### 2.5 Runtime context

Primary file:

- `backend/app/services/ai_runtime_context.py`

Current behavior:

- builds persona context;
- calls retrieval;
- emits `knowledge_context`, `rag_trace`, `safety_policy`;
- serializes evidence pack, hits, locked facts, retrieval metadata, and grounding flags.

Current strengths:

- structured enough for AI runtime and trace;
- supports locked facts contract;
- tracks grounding source and evidence pack.

Current weakness:

- does not expose logistics intent / rewrite trace because those modules do not yet exist;
- locked facts depend on retrieval quality;
- no answer plan is produced here.

### 2.6 WebChat Fast / AI decision runtime

Primary files:

- `backend/app/api/webchat_fast.py`
- `backend/app/api/webchat_fast_v8_patch.py`
- `backend/app/services/webchat_ai_decision_runtime/`
- `backend/app/services/provider_runtime/`
- `backend/app/services/webchat_fast_ai_service.py`

Current strengths:

- PR381 closed old hardcoded fallback leakage;
- provider runtime / codex app server path is active;
- AI decision trace is present;
- tool executor and policy gate exist;
- tracking facts and business action boundaries are partially protected.

Current weakness:

- WebChat Fast is only as good as the evidence provided by knowledge_context;
- answer planning is distributed across services rather than centralized;
- tool boundary, KB boundary, handoff boundary, and clarification boundary should be made first-class.

### 2.7 Tests and CI

Primary areas:

- `backend/tests/test_knowledge_rag_runtime.py`
- `backend/tests/test_knowledge_runtime_context.py`
- `backend/tests/test_webchat_fast_v8_final_api_guard.py`
- `backend/tests/test_webchat_fast_reply_api.py`
- `backend/tests/test_provider_runtime_output_contracts.py`
- `.github/workflows/knowledge-runtime-eval.yml`
- `.github/workflows/knowledge-runtime-pg-hybrid.yml`

Current strengths:

- direct_answer and PR381 final guard have focused tests;
- backend full regression and knowledge runtime CI exist;
- production smoke process has been proven manually.

Current weakness:

- no comprehensive logistics golden set;
- no last-mile wrong-KB competition test suite;
- no formal tool/handoff boundary evaluation matrix;
- no CI gate dedicated to logistics customer-operation scenarios.

## 3. Production evidence from PR381

PR381 production result:

- production image: `nexusdesk/helpdesk:pr381-kb-final-api-guard-v8b-20260604_152251`;
- git sha: `cc03e85266ab909b481fdd7a420bc3378fbb70f7`;
- production health: passed;
- public health: passed;
- KB direct_answer smoke: passed;
- server_safe_fallback removed from direct_answer happy path;
- unwanted handoff/ticket side effect prevented;
- PR merged to main as `0ef1b992d0bafe064c2109f3dd2f5734ecd614ec`.

Last-mile probe result:

- system response was not hardcoded canned fallback;
- AI runtime was active;
- direct_answer technical chain works for strong matches;
- last-mile natural language grounding was not stable;
- retrieval/selector modernization is required.

## 4. Current risk register

| Risk | Severity | Current state | KIR 2.0 mitigation |
|---|---:|---|---|
| Wrong KB selected | High | Observed in last-mile tests | Domain guard + reranker |
| Natural query misses KB | High | Observed in last-mile tests | Intent parser + query rewrite |
| Live tracking answered by policy | High | Guard partially exists | Answer planner + tool boundary |
| Action request answered as FAQ | High | Partially protected | Operation boundary planner |
| Unwanted handoff/ticket | Medium | PR381 improved | Evaluation cases + trace |
| Lack of regression scenarios | High | Partial tests only | Golden case suite |
| Debuggability | Medium | Trace exists but scattered | Unified trace contract |

## 5. Current-state verdict

Current state is production-usable for controlled direct_answer facts and PR381 WebChat Fast happy path. It is not yet production-complete for logistics-domain intelligence.

Required next step: implement KIR 2.0 through PR383-PR386 after PR382 documentation and golden-case specification are accepted.
