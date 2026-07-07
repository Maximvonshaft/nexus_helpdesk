# Customer Visible Message Contract Closed

## Milestone name

Customer Visible Message Contract Closed

## Frozen commit

`bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

## Production image

`nexusdesk/helpdesk:mcs-visible-contract-bcec9cba-20260707T000000Z`

## Production server

34 Switzerland

## Alembic current revision

`20260707_0052`

## Scope frozen

1. AI 客户可见回复必须来自 `provider_runtime` / `ai_runtime`。
2. AI 回复必须带 `runtime_trace_id`、`contract_version`、`runtime_signature`、`safety_status`。
3. v3 payload 通过 `runtime_contract_payload_json` + `runtime_contract_payload_sha256` 穿透。
4. signed AI body 禁止在出站 safety 阶段被静默修改。
5. `business_system` / `tool_service` / `knowledge_runtime` / `safety_service` 不能发送客户可见文本。
6. `handoff_notice` 不再是 contract bypass。
7. WebChat AI / WhatsApp AI / 人工回复 / handoff notice 出站记录统一经过 `CustomerVisibleMessageService`。
8. AI 回复写 `last_ai_update` / `last_runtime_reply_at`，不写 `last_human_update`。
9. legacy originless outbound 默认 fail closed。
10. `effective_country` 已进入 WebChat Runtime Context 和 RAG filter。

## Explicitly not frozen

1. v3 生产全量默认切换。
2. 知识库 Article / Version / ContextBlock 产品化重构。
3. WhatsApp template / service window 完整治理。
4. Voice runtime。
5. 记忆机制正式产品化。

## Operational invariants

1. 任何客户可见 outbound，必须有 `origin`。
2. AI origin 必须有 trace、signature、contract、safety。
3. `human_agent` origin 必须有 `created_by`，且会话状态允许人工回复。
4. Runtime signature 覆盖最终 body。
5. safety 不得在签名后修改 AI body。
6. system event 不能伪装成客户可见消息。
7. `null_reply` 不发送客户可见文本。

## Tag instruction

Create the annotated git tag after this milestone is merged into the target branch:

```bash
git tag -a mcs/customer-visible-contract-closed-bcec9cba \
  bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4 \
  -m "Customer visible message contract closed.\nFrozen at bcec9cba.\nProduction image: nexusdesk/helpdesk:mcs-visible-contract-bcec9cba-20260707T000000Z."

git push origin mcs/customer-visible-contract-closed-bcec9cba
```
