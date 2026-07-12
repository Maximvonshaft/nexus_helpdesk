# Nexus OSR Business Scenario Contract v1

## Purpose

The business scenario catalog is the product-semantics authority for Functional Completeness, End-to-End Capability and Business Loop Closure.

It answers, for each controlled logistics issue type:

- which authoritative facts are required;
- which customer inputs remain missing;
- which governed actions are allowed or required;
- which risk and escalation policy applies;
- which operational/customer/business outcomes must exist;
- when notification is mandatory or may be waived;
- what Business Definition of Done applies;
- whether an observation period must elapse;
- what conditions reopen or reclassify the issue.

It does **not** execute tools, send messages, create a second queue or replace live operational truth.

## Authority boundaries

- Tracking/MCP and approved operational systems remain authoritative for live facts.
- Customer claims, prior AI replies and AI recommendations cannot satisfy `required_fact_classes`.
- `required_capabilities` must resolve against `services.permissions.ALL_CAPABILITIES`.
- Country, Tenant, market, channel and language overlays remain owned by #571. This catalog uses only `inherit_resolved_scope`.
- Tool execution remains behind Tool Policy, Capability, ControlledActionExecutor and Provider/Outbox boundaries.
- Customer-visible output remains behind CustomerVisibleMessageService or the accepted governed outbound contract.

## Identity and resolution

Scenario identity uses exact `scenario_key` or explicit `issue_type_aliases`.

Fuzzy text similarity, embeddings and LLM classification may propose a candidate, but they are never authoritative. Unknown or conflicting identity fails closed.

## Notification semantics

`notification_policy` is independent from technical action and business outcome state.

### `required`

The scenario must:

- require the `notify_customer` action;
- require the `customer_notified` outcome;
- accept only `sent` or `delivered` notification state;
- define no waiver reasons.

### `required_if_contactable`

The scenario:

- allows `notify_customer`, but does not require it unconditionally;
- does not require `customer_notified` as an unconditional outcome;
- accepts `sent`/`delivered`, or `waived:<reason>` where the reason is both globally controlled and allowed by that scenario.

Controlled waiver reasons are:

- `no_contact_method`
- `contact_prohibited`
- `legal_hold`
- `no_customer_impact`

A waiver satisfies only the notification policy. It never fabricates an outbound message, Provider delivery or customer confirmation.

### `optional` and `prohibited`

Optional notification accepts `not_required`, `sent` or `delivered`. Prohibited notification accepts only `not_required` or `prohibited`. Neither may smuggle uncontrolled waiver reasons into the catalog.

## Capability authority

Capability values are imported from `app.services.permissions.ALL_CAPABILITIES`. Unknown values fail catalog validation.

The initial Speedaf capability references are:

- `tool:speedaf.order.update_address:write`
- `tool:speedaf.work_order.create:write`

The catalog does not create a parallel capability vocabulary.

## Cancellation semantics

Cancellation behavior uses a controlled vocabulary:

- `cancel_only_with_reason`
- `cancel_only_before_external_acceptance`
- `cancel_only_with_supervisor_reason`
- `cannot_cancel_after_authorized_decision`
- `cannot_cancel_after_escalation`
- `cancel_only_before_return_acceptance`
- `cannot_cancel_after_financial_posting`
- `cancel_only_before_repair_acceptance`
- `cancel_after_reclassification`

These values describe product semantics only. They do not mutate source records or authorize cancellation.

## Completion evaluation

`evaluate_scenario_readiness` calculates bounded missing requirements and blockers.

A closeable scenario is ready only when:

- required authoritative facts are present;
- required customer inputs are present;
- required governed actions are complete;
- required outcome levels are complete;
- notification policy is satisfied;
- required observation has elapsed;
- no high-risk escalation remains open;
- no `repair_required` state exists.

Technical success cannot be promoted automatically:

- API `200` is not operational completion;
- BackgroundJob `done` is not business resolution;
- Dispatch `dispatched` is not operational completion;
- message `sent` is not customer confirmation;
- Ticket `closed` is not Safe Effective Closure.

`escalate_only` and `reclassify_only` scenarios intentionally remain non-closeable in this evaluator.

## Initial scenario coverage

1. tracking/status inquiry;
2. delivery ETA or delay inquiry;
3. address/contact correction;
4. delivery follow-up/work-order request;
5. failed or repeated delivery attempt;
6. formal complaint;
7. refund/compensation request;
8. legal threat or personal-data request;
9. return/refusal flow;
10. COD/payment anomaly;
11. Operations Dispatch failure/dead-letter;
12. generic missing-information intake and reclassification.

## Downstream integration

### #525 Case Workspace

The Workspace consumes:

- scenario key/version;
- required and missing facts;
- required customer inputs;
- action policy;
- notification policy;
- Definition of Done;
- observation/reopen policy;
- readiness blockers.

The Workspace must still calculate actual permissions and action availability from runtime authority.

### #526 Lifecycle

Lifecycle consumes the active scenario definition together with #587 action outcomes and #589 correlated source identity. It must not close from Ticket status alone.

### #527 Metrics

Metrics use scenario version, Definition of Done, observation window and outcome contract as denominator authority. Historical interpretation must preserve the scenario version that governed the case.

## Validation

Use:

```bash
PYTHONPATH=backend python backend/scripts/validate_business_scenario_catalog.py
PYTHONPATH=backend pytest -q backend/tests/test_business_scenario_catalog.py
```

The CLI emits only a bounded safe summary: schema, version, owner, scope mode, scenario count/keys and source digest. It does not emit scenario bodies, customer inputs or sensitive values.

## Rollout

This contract is additive and inactive until downstream Work Items consume it. It has no migration, route, Provider, customer-send, deployment or production-data effect.
