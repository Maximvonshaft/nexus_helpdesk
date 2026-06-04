# KIR 2.0 Gap Matrix

Status: PR382 gap baseline  
Runtime behavior change: none

## 1. Gap rating scale

| Rating | Meaning |
|---|---|
| 0 | Missing |
| 1 | Prototype |
| 2 | Pilot usable |
| 3 | Production baseline |
| 4 | Strong production |
| 5 | Best-practice target |

## 2. Capability gap matrix

| Capability | Current rating | Target rating | Current evidence | Gap | Planned PR |
|---|---:|---:|---|---|---|
| Knowledge item lifecycle | 3 | 4 | KnowledgeItem / Version / Chunk, publish/index flow | Needs stronger domain metadata, conflict groups, publish-time simulation | PR384 |
| Direct answer facts | 3 | 5 | fact_question / fact_answer / fact_aliases / fact_status / answer_mode | Needs domain intent, answer plan type, operation boundary | PR383-PR385 |
| Basic RAG retrieval | 3 | 5 | hybrid_rag_v2, query analysis, candidate scoring | Needs candidate fusion, query rewrite, reranker, wrong-KB prevention | PR383-PR384 |
| Logistics intent taxonomy | 0 | 5 | No formal logistics ontology | Missing shipment lifecycle and action taxonomy | PR383 |
| Query rewrite / expansion | 1 | 5 | Some term analysis exists | Natural language is not rewritten into logistics event terms | PR383 |
| Dense vector retrieval | 2 | 4 | Embedding/backfill/probe files exist, pg hybrid references exist | Needs explicit production contract and fusion trace | PR384 |
| Structured fact lookup | 3 | 5 | direct_answer facts receive score boost | Needs exact alias candidate path and fact-question priority | PR384 |
| Deterministic reranker | 1 | 5 | Scoring exists inside retrieval | Needs explicit reranker module and trace | PR384 |
| Wrong-KB prevention | 1 | 5 | Not sufficient; last-mile showed wrong KB risk | Needs domain guard, negative penalties, conflict detection | PR384 |
| Grounding selector | 3 | 5 | trusted direct_answer selector exists | Needs domain intent compatibility and action boundary awareness | PR384-PR385 |
| Answer planner | 1 | 5 | Decision logic distributed across services | Need first-class plan: KB/tool/clarify/handoff | PR385 |
| Tool boundary | 2 | 5 | Tool executor/policy gate exist | Need logistics action contract for tracking/address/refusal/cancel/complaint | PR385 |
| Tracking boundary | 3 | 5 | Tracking fact path exists | Need global planner-level boundary and golden tests | PR385-PR386 |
| Handoff boundary | 3 | 5 | Handoff policies exist, PR381 improved unwanted side effects | Need unified handoff planning and scenario tests | PR385-PR386 |
| Evaluation / simulation | 2 | 5 | Local tests and knowledge eval exist | Need 100+ logistics golden cases and CI gate | PR386 |
| Trace / observability | 2 | 5 | evidence_trace and ai_decision_trace exist | Need unified retrieval/reranker/planner trace | PR386 |
| Production rollout | 3 | 5 | PR381 production rollout succeeded | Need KIR-specific rollout gate and rollback plan | PR386 |

## 3. Highest-impact gaps

### 3.1 Logistics intent taxonomy is missing

Current retrieval treats many logistics phrases as plain text. It does not reliably understand operational events such as:

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

Impact: natural customer messages can miss the correct KB or select unrelated KB.

### 3.2 Query rewrite is missing

Current query analysis extracts terms, but it does not rewrite natural customer language into logistics domain terms.

Example:

```text
Input: The courier came while I was not home. Will you deliver again?
Needed rewrite: recipient_absent, missed delivery attempt, failed delivery attempt, redelivery policy, next working day, order verification.
```

Impact: last-mile policy KB may not be retrieved.

### 3.3 Reranker is not explicit enough

Current scoring exists, but it is embedded inside retrieval. It does not act as an explicit, auditable second-stage reranker.

Impact: unrelated KB can outrank the correct KB if it happens to share obvious terms such as city, fee, delivery, or demo.

### 3.4 Wrong-KB prevention is weak

A production AI support runtime must actively block wrong-domain answers.

Examples:

| User question | Must not answer with |
|---|---|
| Recipient not home | Fee/pricing KB |
| Live parcel status | Generic FAQ / policy KB |
| Address change request | Generic address policy without verification/tool boundary |
| Refusal/cancellation | Friendly FAQ only |
| Complaint escalation | Direct answer only |

### 3.5 Evaluation is not scenario-complete

Current tests cover important technical paths, but they are not yet a full logistics operations evaluation suite.

Needed dimensions:

- precise vs natural wording;
- wrong-KB competition;
- tool boundary;
- handoff boundary;
- low-signal negative controls;
- live-status boundary;
- multilingual variants;
- channel/audience/language filtering;
- no unwanted side effects.

## 4. Target production gates

KIR 2.0 cannot be considered production-complete until the following gates exist and pass:

| Gate | Required result |
|---|---|
| Logistics golden cases | 100+ cases pass |
| Last-mile precise query | correct KB direct_answer |
| Last-mile natural query | correct KB direct_answer |
| Wrong-KB competition | unrelated KB rejected |
| Tracking status | tracking tool, not KB policy |
| Address change | verification/tool boundary |
| Refusal/cancel | tool/handoff boundary |
| Complaint | work order/handoff boundary |
| Low-signal hello | no business KB grounding |
| Trace | query intent, rewrite, candidates, reranker, plan visible |
| WebChat Fast | no server_safe_fallback for trusted evidence |
| Side effects | no unwanted ticket/handoff |

## 5. No-regression requirements from PR381

Future PRs must not regress:

- PR381 direct_answer happy path;
- provider_runtime/codex_app_server reply path;
- tracking fact redaction and live-status boundary;
- explicit human handoff behavior;
- refusal/cancel/address-change tool/action behavior;
- public WebChat Fast origin and request contract;
- evidence_trace and ai_decision_trace presence;
- rollback-ready deployment practice.
