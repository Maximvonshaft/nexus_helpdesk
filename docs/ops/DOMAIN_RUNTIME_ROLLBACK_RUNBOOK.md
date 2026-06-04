# Domain Intelligence Runtime Rollback Runbook

Status: draft for PR383
Default recovery: disable feature flags

## 1. Rollback principle

The Domain Intelligence Runtime must be recoverable without database destructive actions. The first rollback action is to disable runtime enforcement flags and return to the PR381 behavior path.

## 2. Fast rollback flags

Set the following values and redeploy/reload configuration using the normal deployment process:

```env
DOMAIN_INTELLIGENCE_ENABLED=false
DOMAIN_INTELLIGENCE_SHADOW_MODE=true
DOMAIN_RETRIEVAL_RERANK_ENABLED=false
DOMAIN_GUARD_ENFORCEMENT_ENABLED=false
DOMAIN_ANSWER_PLANNER_ENABLED=false
DOMAIN_TRACE_ENABLED=true
```

Expected result:

- no domain runtime enforcement;
- no domain-driven tool calls;
- no domain-driven handoff or ticket creation;
- existing WebChat Fast / KB direct answer runtime remains authoritative.

## 3. Code rollback

If disabling flags is insufficient:

1. Revert the deployment to the last known good production image.
2. Preserve rollback container/image discipline from PR381.
3. Do not prune production volumes.
4. Do not delete rollback images during incident response.
5. Do not reset production source tree destructively.

## 4. Data rollback

This PR is designed to avoid destructive data dependency. If future migrations add nullable generic metadata fields, rollback should not require column removal.

Do not remove:

- knowledge items;
- knowledge chunks;
- tickets;
- handoff records;
- audit logs;
- trace records.

## 5. Incident checks

After rollback, verify:

1. `/healthz` passes locally.
2. public health endpoint passes.
3. WebChat Fast normal message works.
4. trusted KB direct_answer still avoids `server_safe_fallback`.
5. low-signal messages do not ground business KB.
6. no unwanted handoff or ticket is created.

## 6. Post-rollback report

Record:

- incident time;
- triggering feature flag values;
- affected tenant/channel;
- sample request IDs;
- before/after traces;
- rollback action taken;
- remaining risk;
- next corrective PR.
