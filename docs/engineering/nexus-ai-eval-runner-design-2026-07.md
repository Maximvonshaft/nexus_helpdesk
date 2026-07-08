# Nexus AI Eval Runner Design

Date: 2026-07-08  
Scope: Eval runner design only. No Runtime calls. No token usage.

## Goals

The eval runner proves whether the answer system avoids “wrong-mouth-to-wrong-question” behavior before production rollout. It must evaluate both M1 context policy and, later, runtime replies.

## Non-goals

- No public AI Runtime call in v1.
- No Runtime token in code, browser, widget, logs, metrics, fixtures, or golden cases.
- No production DB write.
- No changes to CustomerVisibleMessageService or outbound contract.

## Input

- `evals/customer_support_golden_cases_2026_07.json`
- Optional context-package JSON from M1 tests or staging fixtures.
- Optional future runtime reply JSON collected server-side through MCS gateway.

## Option 1: Static Policy Eval

First implementation should not call AI Runtime. It validates whether a case’s context package allows or blocks specific answer classes.

### Checks

| condition | expected policy |
|---|---|
| previous AI reply exists | `previous_ai_reply = not_evidence`, `use = coherence_only` |
| customer claim exists | `customer_message.factuality = customer_claim` |
| tracking intent without tool fact | `live_tracking_answer_allowed=false` |
| tracking intent with tool fact | `live_tracking_answer_allowed=true` |
| KB-only live tracking | blocked |
| wrong-country KB hit | blocked |
| internal-only KB hit | excluded from customer-visible sources |
| expired KB hit | blocked or handoff/clarification |
| low confidence intent | clarification or handoff |
| explicit handoff request | handoff_notice or null_reply |

### Pseudocode

```python
case = load_case()
context = load_context_package(case.id)
policy = derive_policy(context)

assert policy.previous_ai_reply == "not_evidence"
assert policy.customer_message == "customer_claim"
if case.expected.intent == "tracking_status" and not context.tracking_fact_evidence_present:
    assert policy.live_tracking_answer_allowed is False
```

### Output

```json
{
  "case_id": "tracking_no_tool_zh_001",
  "ok": true,
  "policy_result": {
    "live_tracking_answer_allowed": false,
    "forbidden_sources_excluded": true,
    "required_clarification": true
  },
  "violations": []
}
```

## Option 2: Runtime Reply Eval

Future implementation may call AI Runtime only from staging/server-side gateway. Token must remain in server environment or secret file. Browser/widget must never see it.

### Runtime flow

1. Server-side eval service loads case.
2. Gateway builds controlled test prompt/context.
3. Gateway calls Runtime with token from server env/secret file only.
4. Collect structured reply:
   - reply_type
   - text
   - used_sources
   - unsupported_claims
   - confidence
   - model
   - trace_id
5. Check reply against expected:
   - allowed_reply_types
   - forbidden_behaviors
   - required_sources
   - must_not_include
   - unsupported commitments
   - country/channel/language fit

### Forbidden in Runtime Eval

- Do not expose Runtime token to browser or widget.
- Do not write token to logs.
- Do not write raw tracking number to logs.
- Do not include customer text or raw tracking number in metric labels.
- Do not call production public Runtime directly from developer laptop.

### Behavioral validators

| validator | checks |
|---|---|
| ReplyTypeValidator | reply_type is in allowed list. |
| SourceValidator | required_sources exist and forbidden sources absent. |
| LiveStatusValidator | live status claims require tool source. |
| CommitmentValidator | refund/compensation/tax/delivery commitments require tool or official policy. |
| CountryValidator | used country matches effective_country. |
| ChannelValidator | reply length/format fits channel. |
| MemoryValidator | previous AI/customer claim not cited as fact. |
| TextGuardValidator | must_not_include strings/regex absent. |

## Metrics

Do not put raw customer text in labels. Use case id, intent, channel, language, failure_type, and severity only.

Allowed labels:

- dataset_id
- case_id
- intent
- channel
- language
- failure_type
- severity

Forbidden labels:

- user_message
- reply_text
- raw tracking number
- phone/email
- Runtime token
- Authorization header

## CI integration proposal

Phase 1:

- Run JSON schema validation.
- Run static policy eval on fixture context packages.
- Fail on any critical mismatch.

Phase 2:

- Run Runtime Reply Eval only in staging workflow with server-side secret.
- Store redacted artifacts.
- Require zero critical failures before enabling wider rollout.

## Minimal CLI contract proposal

```bash
python -m evals.customer_support_eval_runner \
  --cases evals/customer_support_golden_cases_2026_07.json \
  --mode static-policy \
  --context-dir artifacts/m1_context_packages \
  --format json
```

Exit codes:

- `0`: all cases pass.
- `1`: non-critical failures only.
- `2`: one or more critical failures.
- `4`: invalid dataset or missing context package.

## Redaction rules

- Mask tracking references as suffix/hash.
- Do not print customer message in metric labels.
- Do not write Runtime token, Runtime URL with auth, Authorization header, or raw request body.
- Store evidence snapshots as structured flags rather than raw full transcript whenever possible.
