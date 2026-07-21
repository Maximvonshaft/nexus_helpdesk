# Nexus Generic Agent Playbook Runtime

## Decision

Nexus core is a generic enterprise Agent execution scaffold. Business behavior is configured through published Business Playbooks and governed Tools. A Playbook is the enterprise replacement for the personal term “skill”: it defines applicability, operating instructions and the Tools that may be used.

The core runtime must not contain logistics-, knowledge-, finance-, HR- or reporting-specific routing, fact gates, keyword detectors, repair branches or fallback implementations.

## Canonical flow

1. Build the sanitized Agent context from the immutable Release, published Persona, channel scope, recent conversation, active bulletins and the content-safe Session checkpoint.
2. Resolve published Business Playbooks from the exact `AgentRelease` selected by `AgentDeployment`.
3. Intersect Playbook Tools with the Release runtime policy, caller permissions, registered production handlers and `ToolExecutionPolicy`.
4. Ask the selected Release-bound model profile for one `nexus.agent_turn.v1` object.
5. Validate raw Tool arguments against the canonical Tool contract and JSON Schema.
6. Validate availability, permission, confirmation, write-risk and execution policy.
7. Execute through the one canonical Tool Executor and its worker-owned transaction.
8. Return bounded, redacted committed observations to the model and repeat for the configured bounded number of rounds.
9. Persist content-safe append-only Run events and an expiring Session checkpoint.
10. Emit one customer-visible final response through the single terminal reply authority.

## Configuration authorities

| Concern | Authority |
|---|---|
| Agent authoring and release | `AgentDefinition`, `AgentRelease`, `AgentDeployment` |
| Exact run evidence | `AgentRunSnapshot`, `AgentRun`, `AgentRunEvent` |
| Expiring session summary | `AgentSessionCheckpoint` |
| Persona | `PersonaProfile` and `PersonaProfileVersion` |
| Knowledge | `KnowledgeItem`, published chunks and `knowledge.search` |
| Business Playbook | versioned `AIConfigResource(config_type=playbook)` |
| Enterprise integration | versioned `AIConfigResource(config_type=integration)` |
| Model inference profile | versioned `AIConfigResource(config_type=model_profile)` |
| Runtime policy | versioned `AIConfigResource(config_type=runtime_policy)` |
| Tool contract | canonical `TOOL_CONTRACTS` |
| Tool execution policy | `ToolExecutionPolicyRecord` |
| Tool execution | `nexus_osr/tool_execution_service_core.py` |
| Final fallback | `agent_runtime/terminal_reply.py` |

## Business Playbook contract

A published Playbook contains:

- stable internal name and operator-facing display name;
- precise description of the business capability;
- ordered operating instructions;
- explicit allowed Tool names;
- priority and optional market/channel/language scope;
- draft, publish, version and rollback lifecycle.

Playbooks cannot register a new executor. Every referenced Tool must already exist in the canonical Tool Registry.

## Tool and integration boundary

An enterprise integration is a published HTTP or MCP-over-HTTP manifest. It contains an exact base URL, host allowlist, credential reference, bounded timeout and response size, and a list of strongly typed operations. Operations are validated with Draft 2020-12 JSON Schema. Responses require an explicit projection allowlist before becoming Tool observations.

Secrets are never stored in control-plane JSON. Production resolves credential references only from Secret Files. Integration calls still pass through canonical Tool contracts, permissions, confirmations, `ToolExecutionPolicy`, idempotency and audit. Progressive discovery searches only operations frozen into the current Release and never exposes unmanaged MCP Tools automatically.

## Model and runtime boundary

Published model profiles configure model name, endpoint reference, request shape, temperature, top-p, context length, output bounds and timeout. Runtime policies configure the Tool allowlist, maximum Tool rounds, Provider timeout and whether high-risk writes may be considered.

Frontend configuration cannot weaken deployment hard limits. High-risk writes additionally require the deployment safety switch, Tool permission, execution policy and required confirmation artifacts.

## Session checkpoint boundary

A Session checkpoint is an expiring, Release-bound summary of content-safe operational evidence. It may retain the last intent, terminal action, run status, bounded Tool outcome metadata and handoff state. It never stores raw customer messages, customer-visible replies, prompts, hidden reasoning, credentials, Tool arguments/results or customer identifiers. A checkpoint cannot grant Tool access or replace immutable Release evidence.

## Prohibited architecture

The following must not return:

- static Playbook/Skill manifests in the repository;
- keyword lists that decide whether model text is factually allowed;
- business-domain branches in the Runtime loop;
- pre-model domain API calls selected by application heuristics;
- parallel Tool registries, executors, Specialist runtimes or HTTP transports;
- secrets inside published configuration JSON;
- long-term customer-fact memory or transcript persistence inside the Agent runtime;
- silent failure after an accepted customer message.

## Extension rule

A new business capability adds or publishes a Business Playbook and, when required, registers one canonical Tool contract and production handler. It does not add a branch to the Agent Runtime loop.
