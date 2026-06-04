# Domain Intelligence Runtime

Status: Draft PR implementation baseline
Runtime mode: shadow by default
Production behavior change: none by default

## Objective

NexusDesk must evolve into a generic AI Customer Operations Runtime. The core runtime must not be hardcoded to logistics. Logistics is the first validation domain pack only.

The runtime converts customer language into:

- domain;
- business intent;
- entities;
- query rewrite terms;
- evidence class;
- action boundary;
- ranked knowledge candidates;
- guard decision;
- answer plan;
- auditable trace.

## Core modules

```text
backend/app/services/domain_intelligence/
  schemas.py
  registry.py
  rewrite.py
  query_understanding.py
  candidate_fusion.py
  reranker.py
  domain_guard.py
  answer_planner.py
  action_boundary.py
  trace.py
  flags.py
```

## Domain packs

```text
backend/app/domain_packs/logistics.py
```

Future packs should follow the same contract: ecommerce, SaaS, fintech, local services, insurance, and custom enterprise domains.

## Safety model

The PR must remain safe by default:

- `DOMAIN_INTELLIGENCE_ENABLED=false`
- `DOMAIN_INTELLIGENCE_SHADOW_MODE=true`
- `DOMAIN_RETRIEVAL_RERANK_ENABLED=false`
- `DOMAIN_GUARD_ENFORCEMENT_ENABLED=false`
- `DOMAIN_ANSWER_PLANNER_ENABLED=false`
- `DOMAIN_TRACE_ENABLED=true`

Shadow mode may calculate traces and evaluation results, but must not alter customer-facing production replies unless explicitly enabled in a later rollout.

## Non-goals

- No hardcoded last-mile answers in API handlers.
- No retrieval bypass.
- No default production behavior change.
- No default ticket, handoff, or tool side effects.
- No logistics-only core naming.
