# Private AI Agent Runtime Rollout Runbook

## Scope

This runbook controls rollout of the canonical Nexus Agent Runtime:

`model → Tool request → governed execution → redacted observation → model → customer reply`

The Runtime is domain-agnostic. Shipment tracking, approved knowledge, handoff and future internal Agents are enabled through Skills and registered Tools.

## Canonical identifiers

- scenario: `agent_turn`
- output contract: `nexus.agent_turn.v1`
- primary Provider: `private_ai_runtime`
- Agent loop version: `nexus.agent_runtime.v1`

Do not create routing rules for retired runtime scenarios or output contracts.

## Preflight

1. Confirm the deployed image includes the expected Git commit.
2. Run Alembic through `20260720_0066` or later.
3. Confirm `provider_routing_rules` contains only the canonical `agent_turn` row for each tenant/channel.
4. Confirm the private runtime token is mounted through `PRIVATE_AI_RUNTIME_TOKEN_FILE`; inline production tokens are forbidden.
5. Confirm the configured model endpoint and request shape are compatible.
6. Confirm the Skill registry loads successfully and every referenced Tool is registered.
7. Confirm enabled Tools have valid input schemas, permissions, confirmation requirements and handlers.
8. Confirm `scripts/ci/check_agent_runtime_residue.py` passes.

## Environment

Required Provider variables:

```text
PRIVATE_AI_RUNTIME_ENABLED=true
PRIVATE_AI_RUNTIME_BASE_URL=<runtime origin>
PRIVATE_AI_RUNTIME_DIRECT_PATH=/api/chat
PRIVATE_AI_RUNTIME_REQUEST_SHAPE=ollama_chat
PRIVATE_AI_RUNTIME_DIRECT_MODEL=<approved model>
PRIVATE_AI_RUNTIME_TOKEN_FILE=<mounted secret path>
PROVIDER_RUNTIME_PRIMARY_PROVIDER=private_ai_runtime
PROVIDER_RUNTIME_OUTPUT_CONTRACT=nexus.agent_turn.v1
PROVIDER_RUNTIME_TIMEOUT_MS=15000
```

Agent controls:

```text
NEXUS_AGENT_MAX_TOOL_ROUNDS=3
NEXUS_AGENT_HIGH_RISK_WRITES_ENABLED=false
WEBCHAT_AGENT_ALLOWED_TOOLS=knowledge.search,speedaf.order.query,speedaf.express.track.query,speedaf.order.waybillCode.query,handoff.request.create,ticket.create,timeline.event.create
```

High-risk write Tools must remain disabled unless their individual Provider feature flag, Tool policy, required permission and confirmation workflow are all operational.

## Rollout sequence

1. Apply database migrations.
2. Deploy with Provider Runtime in control mode.
3. Validate health, Provider audit writes and Agent residue checks.
4. Enable shadow traffic and verify that shadow results never become customer-authoritative.
5. Enable a bounded canary percentage.
6. Review Provider success/timeout rate, Agent round count, Tool failure rate, confirmation/permission blocks, visible fallback rate, and write-action audits.
7. Increase canary only after the required gate is green and no silent customer turns are observed.

## Acceptance cases

- greeting produces a final reply without unnecessary Tool calls
- specific shipment request causes the Agent to request the shipment Tool
- Tool success becomes a redacted observation and a subsequent final reply
- Tool unavailable/not-found becomes an observation and a truthful final reply
- approved-knowledge question uses `knowledge.search`
- unknown Tool is blocked
- invalid Tool arguments are rejected by JSON Schema
- confirmation-required write action does not execute without confirmation
- Provider failure produces a customer-visible deterministic fallback
- no accepted customer message ends with an empty UI state

## Rollback

1. Activate the Provider kill switch or return traffic to control mode.
2. Do not restore retired runtime contracts, domain-specific keyword gates or pre-model business lookups.
3. Keep the canonical Skill and Tool data model in place while the Provider is unavailable.
4. Investigate from bounded Provider audits, Agent round traces and Tool call logs.
5. Re-enable through shadow and canary after correction.
