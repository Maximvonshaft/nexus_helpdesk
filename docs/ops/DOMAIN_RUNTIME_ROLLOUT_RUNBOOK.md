# Domain Intelligence Runtime Rollout Runbook

Status: draft for PR383
Production default: disabled / shadow-only

## 1. Scope

This runbook covers the rollout of the generic Domain Intelligence Runtime. The runtime is domain-agnostic. Logistics is only the first validation domain pack.

## 2. Required preconditions

Before enabling any enforcement in production:

1. `Domain Runtime Eval` must pass.
2. `backend-ci` must pass.
3. `backend-full-regression` must pass.
4. Production readiness checks must pass.
5. No hardcoded customer answers may exist in API handlers.
6. No retrieval bypass may be introduced.
7. Shadow traces must show no unwanted ticket, handoff, or tool side effects.

## 3. Default safe configuration

```env
DOMAIN_INTELLIGENCE_ENABLED=false
DOMAIN_INTELLIGENCE_SHADOW_MODE=true
DOMAIN_RETRIEVAL_RERANK_ENABLED=false
DOMAIN_GUARD_ENFORCEMENT_ENABLED=false
DOMAIN_ANSWER_PLANNER_ENABLED=false
DOMAIN_TRACE_ENABLED=true
DOMAIN_EVAL_STRICT_MODE=true
```

## 4. Stage 1: shadow only

Enable trace calculation only where explicitly integrated:

```env
DOMAIN_INTELLIGENCE_ENABLED=false
DOMAIN_INTELLIGENCE_SHADOW_MODE=true
DOMAIN_TRACE_ENABLED=true
```

Expected result:

- customer-facing response remains controlled by the existing PR381 runtime;
- domain trace may be recorded for comparison;
- no tool execution is triggered by the domain runtime;
- no handoff or ticket is created by the domain runtime.

## 5. Stage 2: retrieval comparison

Enable candidate reranking only in non-enforcing comparison mode:

```env
DOMAIN_RETRIEVAL_RERANK_ENABLED=false
DOMAIN_GUARD_ENFORCEMENT_ENABLED=false
```

Expected result:

- existing retrieval result remains authoritative;
- domain reranker output is only evaluated in trace/eval reports.

## 6. Stage 3: limited canary enforcement

Canary enforcement must be limited by tenant, channel, domain, and intent. Do not enable broad production enforcement until golden cases cover the target domain.

Minimum canary requirements:

- one tenant only;
- one channel only;
- selected low-risk policy intents only;
- live status and action requests remain behind tool/verification boundaries;
- rollback owner assigned.

## 7. Stage 4: production enforcement

Only after canary evidence passes:

```env
DOMAIN_INTELLIGENCE_ENABLED=true
DOMAIN_INTELLIGENCE_SHADOW_MODE=false
DOMAIN_RETRIEVAL_RERANK_ENABLED=true
DOMAIN_GUARD_ENFORCEMENT_ENABLED=true
DOMAIN_ANSWER_PLANNER_ENABLED=true
DOMAIN_TRACE_ENABLED=true
```

Production enforcement must preserve:

- PR381 direct answer protections;
- tracking live-status boundary;
- action verification boundary;
- no unwanted ticket/handoff side effects;
- tenant and channel isolation.

## 8. Evidence to collect

Every rollout stage must record:

- commit SHA;
- image tag if deployed;
- feature flag values;
- domain eval report;
- smoke test result;
- failed-case examples;
- rollback command and owner.
