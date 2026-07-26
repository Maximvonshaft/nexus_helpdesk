# Nexus 90-day value-closure scope freeze

## Decision

For the first 90 days after acceptance of the #836 remediation, Nexus prioritizes proof of customer and operator value over new platform breadth.

The allowed product scope is limited to the five journeys in `config/product/golden-journeys.v1.json`:

1. tracking status resolution;
2. delivery delay resolution;
3. address/contact correction;
4. delivery follow-up work order;
5. failed-delivery recovery.

This is not a feature freeze on reliability, security or operations. It is a freeze on new parallel products and speculative abstractions.

## Work that remains allowed

- correctness, availability, latency, accessibility and security remediation;
- production qualification, Provider canaries, recovery rehearsal and incident closure;
- improvements required to complete one of the five Golden Journeys;
- tenant ownership, privacy lifecycle, SLA, evidence, outcome and audit integrity;
- deletion of duplicate, obsolete, compatibility or unreachable implementation;
- instrumentation needed to measure governed business outcomes;
- capacity changes justified by measured saturation or SLO breach.

## Work that is not allowed without an explicit product-contract change

- a second Case, Conversation, Handoff, Queue, SLA, Privacy, Metrics or Release authority;
- a new general-purpose control plane that is not required by a Golden Journey;
- speculative channel or Provider integrations without an approved customer volume and owner;
- duplicate UI, transport, permission, design-system or workflow implementations;
- compatibility layers without a named owner, removal condition and bounded expiry;
- optimization of a component whose business need has not been proven;
- governance activity that only republishes unchanged status.

## Exit criteria

The freeze can be reviewed after 90 days only when the same production candidate lineage can demonstrate:

- zero silent terminal customer outcomes;
- governed Safe Effective Closure measurement with valid denominators;
- trend evidence for first-contact resolution, repeat contact and reopen rates;
- operational completion and customer-notification evidence for controlled actions;
- measured Provider failure, handoff wait and human-touch load;
- completed backup/restore, rollback and incident exercises;
- no unresolved P0/P1 issue in the five Golden Journeys.

A review may expand scope only through the `business_product` Gate in `config/governance/delivery-gates.v1.json`. Expansion cannot create a parallel authority.
