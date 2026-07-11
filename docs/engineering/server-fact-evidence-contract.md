# Server-owned outbound fact evidence

Customer-visible factual statements are authorized by the server, not by a client boolean.

The manual reply path resolves a short-lived `CaseContextRecord` in the same tenant, ticket and conversation scope. A fact is accepted only when it is active, unexpired, recent, PII-redacted, primary-authority, available, fresh, successful and contradiction-free. Tracking hashes must match the current case.

`has_fact_evidence` remains accepted temporarily for backward-compatible request parsing but has no authority. New clients must omit it. `evidence_reference_id` may select a specific in-scope Case Context; when omitted, the server resolves the latest valid active context.

The response and durable audit contain only bounded evidence metadata such as the Case Context identifier, reason, authority, evidence state, freshness and safe tracking reference. They never contain the raw tracking number or provider payload.
