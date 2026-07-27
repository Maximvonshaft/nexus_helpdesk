# Nexus 90-day value-closure scope freeze

## Decision

For the first 90 days after acceptance of the #838 remediation, Nexus prioritizes proof of customer and operator value over new platform breadth.

`config/product/golden-journeys.v1.json` owns **selection and ordering only**. The sole executable business definition for facts, inputs, actions, outcomes, notification, closure, lifecycle and metrics is `backend/app/config/business_scenarios.v1.json`, loaded by the canonical Business Scenario service.

The selected scope is limited to:

1. `tracking_status_inquiry`;
2. `delivery_eta_delay_inquiry`;
3. `address_contact_correction`;
4. `delivery_followup_work_order`;
5. `failed_repeated_delivery_attempt`.

No second journey document may restate those scenarios' Definition of Done, action list, terminal outcome, metric contract or failure behavior.

This is not a feature freeze on reliability, security or operations. It is a freeze on new parallel products and speculative abstractions.

## Work that remains allowed

- correctness, availability, latency, accessibility and security remediation;
- production qualification, Provider canaries, recovery rehearsal and incident closure;
- improvements required by one of the five selected Business Scenario definitions;
- tenant ownership, privacy lifecycle, SLA, evidence, outcome and audit integrity;
- deletion of duplicate, obsolete, compatibility or unreachable implementation;
- instrumentation needed to measure governed business outcomes;
- capacity changes justified by measured saturation or SLO breach.

## Work that is not allowed without an explicit product-contract change

- a second Case, Conversation, Handoff, Queue, SLA, Privacy, Metrics or Release authority;
- a second Golden Journey or Business Scenario definition for the same scenario;
- a new general-purpose control plane that is not required by a selected scenario;
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
- no unresolved P0/P1 issue in the five selected scenarios.

A review may expand scope only through the `business_product` Gate in `config/governance/delivery-gates.v1.json`. Expansion cannot create a parallel authority.
