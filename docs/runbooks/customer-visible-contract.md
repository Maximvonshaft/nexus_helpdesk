# Customer Visible Message Contract Runbook

This runbook covers the frozen customer-visible message boundary after milestone `Customer Visible Message Contract Closed`.

## 1. How to judge whether a customer-visible message is compliant

A customer-visible outbound message is compliant only when all of the following are true:

1. `origin` is present.
2. AI-origin messages use only `provider_runtime` or `ai_runtime`.
3. AI-origin messages include `runtime_trace_id`, `runtime_contract_version`, `runtime_signature`, `safety_status`, and a matching `runtime_contract_payload_sha256` when `runtime_contract_payload_json` is present.
4. `human_agent` messages have `created_by` and the conversation state permits human reply.
5. Signed AI body is the final body that reaches outbound dispatch.
6. `null_reply` is recorded as runtime state only and never creates customer-visible text.
7. System events and handoff state transitions are not represented as customer-visible text unless the text came from a valid AI runtime contract.

Forbidden customer-visible origins:

- `business_system`
- `tool_service`
- `knowledge_runtime`
- `safety_service`
- `handoff_notice`

## 2. How to run the DB audit script

Default 24-hour JSON audit:

```bash
python scripts/audit_customer_visible_contracts.py --hours 24 --format json
```

Human-readable audit:

```bash
python scripts/audit_customer_visible_contracts.py --hours 24 --format text
```

The script exits with code `2` when non-zero risk is found. Use that behavior in daily cron, CI, or deployment gate jobs.

## 3. How to run the smoke script

Production or staging read-only smoke:

```bash
python scripts/smoke_customer_visible_contracts.py \
  --base-url https://www.leakle.com \
  --channel webchat \
  --message "你好" \
  --expect-ai-reply
```

With DB audit chained after smoke:

```bash
python scripts/smoke_customer_visible_contracts.py \
  --base-url https://www.leakle.com \
  --channel webchat \
  --message "你好" \
  --expect-ai-reply \
  --audit-db
```

Local contract simulations can run without a public URL:

```bash
python scripts/smoke_customer_visible_contracts.py --channel webchat --message "转人工"
```

## 4. Handling `missing_customer_visible_origin_contract`

Meaning: an outbound row had no `origin`, no runtime contract, and no human actor. It cannot be safely attributed.

Required handling:

1. Keep the row dead; do not dispatch it.
2. Inspect `ticket_id`, `conversation_id`, `outbound_id`, and surrounding ticket events.
3. Identify the code path that created the row without origin.
4. Fix the producer to set a valid origin and contract fields before any requeue.

Not allowed:

- Do not directly update DB to fill `origin` and replay unless the source can be proven.
- Do not enable `ALLOW_LEGACY_ORIGINLESS_OUTBOUND` as a long-running workaround.

## 5. Handling `runtime_signed_body_mutation`

Meaning: the AI reply was signed, but the safety layer attempted to normalize or alter the customer-visible body after signature.

Required handling:

1. Keep the outbound dead.
2. Compare `runtime_signature`, `runtime_contract_version`, `runtime_trace_id`, and the final stored `body`.
3. Inspect the safety decision that produced `normalized_body`.
4. Fix safety to block or require review before signing, not after signing.
5. Re-run smoke scenario `Signed body mutation simulation`.

Not allowed:

- Do not silently send the normalized body with the old signature.
- Do not mutate a signed AI body after runtime contract creation.

## 6. Handling v3 answer without `used_sources`

Meaning: a v3 `answer` attempted to go customer-visible without grounding.

Required handling:

1. Keep the message blocked.
2. Confirm whether the reply should have been `clarifying_question`, `handoff_notice`, or `null_reply` instead of `answer`.
3. If it is a tool answer, include a tool source in `grounding.used_sources`.
4. If it is a KB answer, include a KB chunk source and authority metadata.
5. For high-risk answers, use `official_policy` or tool source.

## 7. How to temporarily close v3 gray rollout

Set:

```bash
AI_REPLY_V3_ENABLED=false
AI_REPLY_V3_GREETING_ENABLED=false
AI_REPLY_V3_HANDOFF_ENABLED=false
AI_REPLY_V3_TOOL_ANSWER_ENABLED=false
AI_REPLY_V3_KB_ANSWER_ENABLED=false
```

Restart app/worker processes after changing environment variables.

Do not force production to v3 globally. The default remains v2 unless the rollout flags permit a specific reply type and channel.

## 8. Why not to open `ALLOW_LEGACY_ORIGINLESS_OUTBOUND`

`ALLOW_LEGACY_ORIGINLESS_OUTBOUND=true` reopens the historical bypass where customer-visible text can leave the system without a responsible origin or contract. That breaks auditability, makes safety mutation unprovable, and can send system-generated text as if it were valid customer communication.

Use it only for a time-boxed emergency rollback with explicit incident owner, start time, end time, and compensating DB audit.

## 9. How to confirm AI did not pollute `last_human_update`

Run:

```bash
python scripts/audit_customer_visible_contracts.py --hours 24 --format text
```

The `ai_human_field_pollution` check must be zero.

Manual SQL pattern:

```sql
select id, ticket_no, last_runtime_reply_at
from tickets
where last_runtime_reply_at >= now() - interval '24 hours'
  and last_human_update is not null
  and last_ai_update is not null
  and last_human_update = last_ai_update;
```

Expected result: no rows.

## 10. How to confirm handoff request did not send hardcoded ack

Check the relevant conversation and outbound rows:

1. Handoff state should be represented by `WebchatHandoffRequest`, ticket events, and webchat system events.
2. No customer-visible row should use `origin='handoff_notice'`.
3. If a customer-visible handoff notice exists, it must use `origin in ('provider_runtime', 'ai_runtime')`, v3 `reply.type='handoff_notice'`, valid trace, signature, safety, and payload hash.

Manual SQL pattern:

```sql
select id, ticket_id, origin, runtime_contract_version, runtime_reply_type, created_at
from ticket_outbound_messages
where created_at >= now() - interval '24 hours'
  and (origin = 'handoff_notice' or runtime_reply_type = 'handoff_notice')
order by created_at desc;
```

Expected result:

- `origin='handoff_notice'`: zero rows.
- `runtime_reply_type='handoff_notice'`: allowed only with AI origin and v3 contract.

## Hard prohibitions

- Do not directly fix DB origin and replay unless the source is provable.
- Do not open the legacy originless switch for long-term operation.
- Do not use `safety_service` output as customer-visible wording.
- Do not allow tool services to return natural-language text that is sent directly to customers.
- Do not turn system events into customer-visible text.
- Do not use `handoff_notice` as a customer-visible origin.
