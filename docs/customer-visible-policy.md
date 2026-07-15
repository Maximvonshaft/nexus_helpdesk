# Customer-Visible Message Policy

Nexus has one customer-visible message boundary: `CustomerVisibleMessageService`.

AI messages must pass the signed `nexus.ai_reply.v3` contract before creation or dispatch. The gateway verifies the Runtime origin, trace ID, contract version, HMAC signature, exact signed body, reply type, used sources, unsupported claims, conflicts, confidence, and channel. `null_reply` is recorded but never sent.

The content policy does not infer business meaning from reply text. It only blocks empty or oversized messages and direct disclosure of credentials or internal reasoning. Logistics truth is enforced through structured tool evidence, knowledge authority metadata, and the Runtime contract rather than keyword lists.

Human replies require an authenticated actor and an active takeover state. They pass the same malformed-content and disclosure checks but do not require an AI contract.

External dispatch remains fail closed when the provider, route, target, origin, or AI contract is missing or invalid.
