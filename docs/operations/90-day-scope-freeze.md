# Nexus 90-Day Value-Closure Scope Freeze

## Decision

For the 90-day period governed by `config/business/golden-journeys.v1.json`, Nexus delivery is limited to closing five approved logistics customer journeys, fixing P0 customer/security/privacy/data-integrity defects, and deleting duplicate or legacy paths that prevent those outcomes.

This is a product and engineering constraint, not a release authorization. Full Production remains fail closed under the capability-specific activation authority.

## Allowed work

1. Close a missing acceptance item in one of the five Golden Journeys.
2. Fix a P0 customer-visible terminal failure, security issue, privacy issue, data-integrity defect or production incident.
3. Remove a duplicate writer, parallel product surface, obsolete compatibility layer or historical residue required to complete items 1 or 2.
4. Add bounded evidence, observability, recovery or rollback required by the five event-driven delivery Gates.

## Work that is frozen

- a new operator product or navigation surface;
- a second Case, Conversation, Handoff, queue, SLA, privacy, metrics or Agent authority;
- a generic platform abstraction without a current Golden Journey consumer;
- a new Provider integration without a bounded Journey and activation plan;
- speculative workflow automation, dashboards or configuration frameworks;
- an unbounded compatibility layer or dual-write period;
- a production enablement claim based only on source presence or green CI.

## Required change record

Every delivery must identify:

- `journey_key` or a P0/incident identifier;
- the real customer or operator problem;
- the single aggregate writer and Tenant authority;
- customer-visible terminal outcome;
- metric, denominator and target;
- failure recovery and rollback;
- superseded code or state removed in the same delivery;
- exact immutable candidate evidence.

## Exit criteria

The freeze ends only after all five Golden Journeys have:

- executable end-to-end acceptance;
- zero silent customer terminal outcomes;
- structured fact, action, Provider receipt, operational outcome, notification and closure evidence where applicable;
- an operational owner and recovery path;
- business metrics with denominators, targets and trend;
- bounded activation and accountable GO/NO-GO evidence on one exact source SHA and image digest.

A green repository gate alone does not end the freeze.
