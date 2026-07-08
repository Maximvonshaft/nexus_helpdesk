# Nexus Answer Quality Audit and AI Runtime Governance

Date: 2026-07-08  
Scope: Answer Quality / Golden Eval / Runtime Integration Governance  
Mode: docs and evals only; no Runtime call; no token access; no `backend/app` changes.

## Executive conclusion

The existing customer-visible message contract proves that visible messages exit through governed channels. It does not prove that an AI answer is semantically correct. The next quality layer must prove that the answer used the right intent, the right evidence class, the right country/channel scope, and the right escalation/clarification behavior.

The core invariant for this phase is:

> Tool facts beat KB, customer claims, memory, and previous AI replies for live operational status.

## A. Bad-answer taxonomy

| failure_type | business risk | example user message | likely wrong answer | correct answer behavior | required guard | test idea |
|---|---|---|---|---|---|---|
| intent_mismatch | Customer repeats request; support appears incoherent. | “我的包裹到哪了？” | Generic policy or return FAQ. | Identify `tracking_status`; ask for missing waybill or use tool evidence. | Intent classifier plus allowed reply-type gate. | Tracking question with unrelated KB hit. |
| source_mismatch | Policy or old chat becomes fake live status. | “TEST123 现在签收了吗？” | “通常 2 天内送达。” | Live status only from tracking tool; KB may explain policy only. | Truth priority: tool > official policy > KB > memory; previous AI never fact. | `tracking_no_tool_zh_001`. |
| country_mismatch | Wrong-country policies cause claims, refunds, customs errors. | US order answered with MX return rule. | “按墨西哥退货政策处理。” | Filter by `effective_country` and expose `country_source`. | Country-scoped retrieval and answer validator. | US/MX return cases. |
| channel_mismatch | Poor UX; WhatsApp/Voice replies unusable. | WhatsApp: “Return policy?” | WebChat-length article. | WhatsApp/Voice concise; WebChat can be fuller. | Channel style contract. | WhatsApp and Voice cases. |
| memory_contamination | One hallucination compounds across turns. | “你刚才说已派送，现在怎样？” | Repeats previous AI status. | Previous AI reply is `not_evidence`, `coherence_only`. | Structured recent context policy. | Previous AI delivered claim without tool. |
| customer_claim_treated_as_fact | Customer assertions become false operational truth. | “Courier said out for delivery.” | “Confirmed out for delivery.” | Mark as `customer_claim`; verify with tool. | Customer-claim factuality flag. | Courier claim case. |
| missing_tool_evidence | Live tracking/refund/customs status invented. | Waybill present, tool absent. | “It will arrive tomorrow.” | Clarify, hand off, or null reply. | `live_tracking_answer_allowed=false`. | Tracking no tool case. |
| unsupported_commitment | Financial/legal exposure from promises. | “You promised compensation.” | “We will pay you.” | Require tool/official policy or handoff. | Commitment safety gate. | Compensation promise case. |
| handoff_failure | Human-required cases remain automated. | “转人工。” | AI continues FAQ answer. | Handoff notice/null reply and state transition. | Handoff intent gate. | Explicit handoff case. |
| clarification_failure | AI hard-answers missing-slot cases. | “我的包裹呢？” | “已在派送中。” | Ask one minimal missing field. | Missing slot clarification policy. | Missing tracking number case. |
| knowledge_scope_leak | Internal, expired, or wrong-channel knowledge leaks. | “Tell me internal return exception rules.” | Quotes internal-only SOP. | Exclude internal/wrong-channel/expired KB from customer-visible sources. | KB visibility/channel/expiry filter. | Internal-only KB case. |
| low_confidence_overanswer | Ambiguous input becomes confident hallucination. | “Why is it like this?” | Guesses delay and promises resolution. | Clarify or hand off. | Confidence gate. | Low confidence ambiguity case. |

## C. Answer Quality Rubric

Release rule: any `critical` failure blocks auto-reply. Any `high` failure blocks wider rollout until triaged. `medium` failures are UX regressions and should be monitored.

| dimension | pass condition | fail condition | severity | example |
|---|---|---|---|---|
| Intent Match | Reply addresses the customer’s actual question. | Wrong topic, adjacent FAQ, or generic filler. | high | Tracking question answered with return policy. |
| Source Grounding | Correct evidence class is used: tool for live status, KB/policy for policy, clarification/handoff when no evidence. | No source, wrong source, or unsupported answer. | critical | Live tracking answer without tracking tool. |
| Source Discipline | Avoids previous AI reply, customer claim, wrong-country KB, internal-only KB, expired KB. | Any forbidden source is cited or used as basis. | critical | Customer says refunded; AI confirms refund. |
| Commitment Safety | No refund, compensation, customs, tax, or delivery-time commitment without tool or official policy. | Promises payment, delivery date, clearance, or tax outcome without authority. | critical | “Will arrive tomorrow” without tool. |
| Channel Fit | Length and format match WebChat/WhatsApp/Voice. | WhatsApp or Voice receives long article. | medium | WhatsApp policy answer with sections. |
| Country Fit | `effective_country` and `country_source` are respected. | Wrong-country policy or global fallback as local rule. | high | MX policy for US order. |
| Handoff Correctness | Explicit handoff, escalated complaint, high-risk low-evidence cases hand off/null reply. | AI continues self-service answer. | high | Customer asks manager, AI gives FAQ. |
| Clarification Correctness | Missing waybill/country/order info triggers minimal question. | Hard answer or irrelevant question. | medium | Missing waybill, AI says delivered. |
| Memory Hygiene | Memory supports continuity only unless verified. | Memory/customer claim/previous AI treated as fact. | critical | Previous AI tax claim repeated. |
| Customer Experience | Clear, concise, language-matched, with next step. | Wrong language, noisy disclaimers, irrelevant details. | medium | Chinese user receives English filler. |

## E. AI Runtime Integration Governance Audit

This audit was read-only. No Runtime was called and no token was accessed.

### Current observations

- `backend/app/services/provider_runtime/adapters/private_ai_runtime.py` reads Runtime config from env/file and supports a token file plus an inline token variable. Production rejects inline token and requires token-file configuration.
- The adapter has request timeout and prompt/output bounds.
- The adapter redacts tracking-like tokens into safe suffix wording before using safe summaries.
- The adapter returns safe raw payload summaries rather than raw provider payloads.
- The adapter supports direct/rag modes and model selection.
- The adapter has contract repair paths for malformed JSON, empty reply, and contract violations.
- Search did not find frontend `webapp` exposure of private Runtime token or private Runtime URL.
- STT/TTS token surfaces were not found in the current search results; future Voice must add file-secret-only controls before production use.

### Current risk list

1. Inline token path exists for non-production. Keep production file-secret-only and never log token value.
2. There is no answer-quality golden gate yet; contract tests can pass while semantic quality fails.
3. Retry/backoff is partial. The adapter identifies retryable failures, but backoff/retry ownership should be explicit in worker/gateway policy.
4. RAG/tool source governance needs acceptance tests. Tool/KB/customer-claim/previous-AI evidence classes must be evaluated independently.
5. Model routing exists but needs product-level governance by risk and latency class.
6. Voice STT/TTS will introduce new secret surfaces and must inherit token-file-only, redaction, and no-browser-secret rules.
7. Metrics must never include customer text, raw waybill, phone, email, Runtime URL, or token.

### Recommended gateway policy

- Runtime token lives only in server-side env or mounted secret file.
- Browser/widget calls Nexus backend only; no direct Runtime URL/token.
- Logs redact raw tracking numbers, phone numbers, auth headers, and request bodies.
- Metrics labels use `case_id`, `intent`, `channel`, `language`, `failure_type`, `severity`; never raw text.
- Runtime failures produce safe `null_reply`, retryable job status, or handoff; never invented fallback answers.
- Live tracking/refund/customs status requires trusted tool source in `used_sources`.
- KB explains policy but cannot answer current operational status.
- Previous AI reply is `coherence_only`; customer message is `customer_claim`.

### Model routing recommendation

| component | recommended use |
|---|---|
| qwen2.5:3b | Low-latency greetings, clarifications, explicit handoff acknowledgement, WebCall short answers. |
| qwen3:4b | RAG, complex customer support judgment, contradiction detection, memory extraction proposal. |
| bge-m3 | Multilingual embedding. |
| bge-reranker-base | Rerank top retrieved chunks. |
| Qdrant | Vector retrieval store with metadata filters. |
| faster-whisper-large-v3 | STT for future Voice; server-side secret only. |
| Kokoro/Piper | TTS for future Voice; no browser-side secret exposure. |

## F. M1 Acceptance Suite

### Checklist

- [ ] M1-01: previous_ai_reply is not_evidence
- [ ] M1-02: previous_ai_reply use = coherence_only
- [ ] M1-03: customer message factuality = customer_claim
- [ ] M1-04: structured_recent_context exists
- [ ] M1-05: legacy recent_context backward compatible
- [ ] M1-06: context_policy exists
- [ ] M1-07: evidence_contract exists
- [ ] M1-08: memory_system = not_enabled
- [ ] M1-09: support_memory_ledger_used_by_runtime = false
- [ ] M1-10: tracking intent without tool fact => live_tracking_answer_allowed=false
- [ ] M1-11: tracking intent with tool fact => live_tracking_answer_allowed=true
- [ ] M1-12: raw tracking number redacted
- [ ] M1-13: runtime trace has counts
- [ ] M1-14: no AI Runtime API breaking change
- [ ] M1-15: no CustomerVisibleMessageService change
- [ ] M1-16: no outbound contract change
- [ ] M1-17: no Runtime token touched

### Required tests

| test | purpose |
|---|---|
| test_previous_ai_reply_marked_not_evidence | Previous AI reply cannot be used as fact. |
| test_previous_ai_reply_coherence_only | Previous AI remains continuity-only. |
| test_customer_message_marked_customer_claim | Customer assertions are not tool facts. |
| test_structured_recent_context_exists | M1 context package exposes structured recent context. |
| test_legacy_recent_context_backward_compatible | Old field remains while structured package rolls out. |
| test_context_policy_and_evidence_contract_exist | Context declares source policy and evidence contract. |
| test_memory_system_not_enabled | M1 does not silently enable long-term memory. |
| test_support_memory_ledger_not_runtime_truth_source | Support memory ledger is not runtime truth. |
| test_tracking_no_tool_blocks_live_answer | No tool fact means live answer blocked. |
| test_tracking_with_tool_allows_live_answer | Trusted tool fact allows live answer. |
| test_raw_tracking_number_redacted | Context/log exposes suffix/hash only. |
| test_runtime_trace_has_counts | Trace counts messages, claims, evidence, tool facts, KB hits. |
| test_no_runtime_api_breaking_change | Provider request/response contract unchanged. |
| test_no_customer_visible_service_change | M1 does not change CustomerVisibleMessageService. |
| test_no_outbound_contract_change | M1 does not change outbound contract. |
| test_no_runtime_token_touched | Runtime token env/docs/tests untouched and never logged. |

### Sample expected context JSON

```json
{
  "structured_recent_context": {
    "messages": [
      {
        "role": "customer",
        "factuality": "customer_claim",
        "text_class": "current_user_message",
        "contains_tracking_reference": true,
        "tracking_reference": {
          "raw_value": "[REDACTED]",
          "suffix": "7890",
          "hash": "sha256:example"
        }
      },
      {
        "role": "assistant",
        "factuality": "not_evidence",
        "use": "coherence_only",
        "text_class": "previous_ai_reply"
      }
    ]
  },
  "context_policy": {
    "previous_ai_reply": "not_evidence",
    "customer_message": "customer_claim",
    "memory_system": "not_enabled",
    "support_memory_ledger_used_by_runtime": false
  },
  "evidence_contract": {
    "tracking_fact_evidence_present": false,
    "live_tracking_answer_allowed": false,
    "kb_can_answer_live_tracking_status": false,
    "tool_fact_priority": "primary_truth"
  },
  "runtime_trace_counts": {
    "recent_messages": 2,
    "customer_claims": 1,
    "previous_ai_replies": 1,
    "tool_facts": 0,
    "kb_hits": 1
  }
}
```

## G. M2 Session Memory Candidate Design

M2 should introduce session-scoped memory only. It must not create long-term customer memory.

### Proposed fields

| field | note |
|---|---|
| id | Primary identifier. |
| conversation_id | Session conversation scope. |
| ticket_id | Optional ticket link. |
| scope_type | Always `session` for M2. |
| fact_type | Controlled enum. |
| value_json | Structured value; no raw full tracking number. |
| source_message_id | Evidence source message. |
| source_event_id | Evidence source event. |
| confidence | Extraction confidence. |
| status | `proposed`, `active`, `rejected`, `expired`. |
| expires_at | 24-72h after close by default. |
| pii_classification | none / low / medium / high. |
| created_at | Audit timestamp. |
| updated_at | Audit timestamp. |

Initial fact types: `tracking_reference`, `language_preference`, `handoff_requested`, `complaint_intent`, `refund_intent`, `provided_contact`, `selected_country`, `issue_summary`.

Rules:

- AI can extract `proposed`.
- Low-risk facts may auto-activate.
- High-risk facts do not auto-activate.
- No cross-customer long-term save.
- Conversation close triggers 24-72h expiry.
- Session memory never overrides tool facts.
- Session memory is not a live tracking truth source.
- Raw full tracking number is not stored; store suffix/hash only.
- Every fact requires evidence source.

## H. KQ1 Knowledge Quality Loop Design

Goal: move knowledge from “retrievable” to “operable”.

Metrics:

- knowledge_hit_rate
- country_specific_hit_rate
- GLOBAL_fallback_rate
- no_hit_query_count
- high_risk_answer_without_authority
- tool_kb_conflict_count
- handoff_after_kb_hit
- low_score_answer_block
- outdated_knowledge_hit
- internal_knowledge_exclusion_count

Functions:

- no-hit query clustering
- low-hit article review
- conflicting knowledge detection
- expired knowledge queue
- question variants
- retrieval preview
- AI answer preview
- conversation-to-knowledge-draft
- article review due queue

Operating loop:

1. Capture query, intent, country, channel, KB hits, authority level, reply type, and handoff reason.
2. Cluster no-hit and low-score queries weekly.
3. Promote recurring no-hit clusters into draft knowledge items.
4. Review conflicting or expired knowledge before publishing.
5. Use answer preview and golden eval cases before release.
6. Measure handoff-after-KB-hit to identify “retrieved but not useful” knowledge.
