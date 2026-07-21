# Nexus Canonical Agent Runtime Operating System

Status: **Canonical**  
Runtime authority: `backend/app/services/agent_runtime/runtime.py`  
Control plane authority: `/agent-control`  
Release authority: `AgentDefinition → AgentRelease → AgentDeployment → AgentRunSnapshot`

## 1. Purpose

Nexus provides one enterprise Agent operating system for customer and operator
work. It deliberately separates immutable product configuration, live execution,
external Tool side effects and safe operational evidence.

This architecture absorbs the useful runtime engineering patterns of modern
Agent harnesses without introducing arbitrary plugins, shell hooks, unmanaged
background services, file-based production configuration or a second Agent
runtime.

## 2. Canonical authorities

| Concern | Single authority |
|---|---|
| Agent authoring | `AgentDefinition` |
| Immutable release | `AgentRelease` |
| Scope and Canary selection | `AgentDeployment` |
| Exact configuration evidence | `AgentRunSnapshot` |
| Operational lifecycle | `AgentRun` |
| Append-only execution evidence | `AgentRunEvent` |
| Runtime loop | `agent_runtime/runtime.py` |
| Context budgeting | `agent_runtime/context_compiler.py` |
| Short-lived session state | `AgentSessionCheckpoint` |
| Tool contracts | `TOOL_CONTRACTS` |
| Tool policy | `ToolExecutionPolicyRecord` / `tool_execution_policies` |
| Tool execution | `nexus_osr/tool_execution_service_core.py` |
| Enterprise integrations | `agent_integration_service.py` |
| Provider routing | `provider_runtime/router.py` |
| Private model transport | `provider_runtime/adapters/private_ai_runtime.py` |
| Operator product | `/agent-control` |

No module may create a parallel definition, release resolver, Tool registry,
Tool executor, Integration transport, Provider router, Agent loop or operator
product.

## 3. Runtime correctness

### 3.1 Timeout authority

The effective Provider timeout is the minimum of:

1. immutable Agent Runtime Policy request timeout;
2. Provider routing safety ceiling;
3. Model Profile timeout.

A lower layer may tighten a timeout but may never widen an upper-layer safety
boundary. The effective value is included in safe Provider audit evidence.

### 3.2 Context Compiler

The Runtime never tail-slices a serialized Prompt. The Context Compiler:

- computes a transport and context-window budget;
- retains the current customer request, language, exact Release identity,
  Runtime Policy, channel context and latest Tool observations;
- bounds or omits optional Persona, Playbook, Tool, Bulletin, Checkpoint and
  recent-conversation sections as complete JSON values;
- emits a digest and content-free size/compaction evidence;
- rejects execution when mandatory truth cannot fit the configured budget.

The same compiler handles Parent Agent and read-only Specialist contracts. It
does not expose hidden reasoning or raw backend payloads.

### 3.3 Blocking I/O boundary

The canonical Tool execution unit remains synchronous because it owns the
transactional SQLAlchemy Session and existing provider clients. The entire
sequential unit is awaited through one worker thread so blocking HTTP cannot
stall the ASGI event loop. The Session is never accessed concurrently.

## 4. MCP control plane

Nexus supports stable MCP lifecycle semantics through the existing Integration
authority:

1. `initialize`;
2. protocol and capability validation;
3. `notifications/initialized`;
4. paginated `tools/list` for diagnosis;
5. release-configured `tools/call` execution;
6. one bounded re-initialization after an invalid session response.

Runtime permissions are never derived from live discovery. The immutable Agent
Release remains authoritative for operation name, read/write classification,
risk, confirmation, input schema and result projection. Unmanaged discovered
Tools are quarantined and reported by MCP Doctor.

MCP Doctor validates connection, protocol, Tool presence and input-schema drift
from the exact currently deployed Release. It cannot publish configuration,
change permissions or execute a business operation.

## 5. Agent Run event source

`AgentRunSnapshot` and `AgentRunEvent` have different duties:

- Snapshot proves which immutable configuration was selected.
- Run and RunEvent describe what safely happened during execution.

Events are sequence ordered and append-only. Event types and payload fields use
closed allowlists. Event persistence forbids:

- raw Prompts or messages;
- hidden reasoning or thoughts;
- Tool arguments or raw Tool results;
- Provider raw payloads;
- credentials, tokens or cookies;
- customer phone, email, address, tracking number or waybill.

Metrics are projected from the same lifecycle. They do not constitute another
event store.

## 6. Session checkpoints

A Session Checkpoint is short-lived operational state, not customer memory.

It is:

- tenant-, session- and Release-scoped;
- versioned and expiring;
- generated only from safe terminal Run events;
- limited to last intent, final action, Run status, round count, handoff state
  and bounded Tool outcome status;
- invalidated by a new version or a different Release.

It never stores raw messages, replies, Prompts, Tool arguments/results,
identifiers, credentials or hidden reasoning. The Runtime can proceed without a
Checkpoint, and Checkpoint persistence failure cannot convert a successful
customer operation into a failed side effect.

## 7. Constrained Specialists

`specialist.delegate` is one read-only Tool in the canonical Tool Registry and
Executor. It is not a second Agent Runtime.

Approved Specialists:

- `knowledge_researcher`;
- `policy_reviewer`;
- `case_summarizer`;
- `translation_reviewer`;
- `data_analyst`.

The Parent Agent selects a fixed Objective enum. Nexus generates the actual
bounded task from request-local context so free customer text is not persisted
as Tool arguments. The Specialist:

- uses the same Release-bound Model Profile and Runtime Policy;
- routes through the same Provider Router and audit authority;
- has no Tool calls, write permissions or customer-visible reply;
- returns only the closed `nexus.agent_specialist.v1` evidence contract;
- is rejected if output contains secrets, hidden reasoning or customer and
  operational identifiers.

The Parent Agent remains the sole decision and response authority.

## 8. Playground Fork and Replay

Fork and Replay invoke `run_agent_with_db`; they do not simulate the Runtime.
Both expose read-only Tools only.

- Playground Fork uses the current Deployment selected by the canonical
  Resolver.
- Replay requires the current scope to resolve to the parent Run's exact
  immutable Release manifest digest. Configuration drift returns a conflict and
  is never silently approximated.

Every Fork or Replay creates a normal `AgentRun` with `parent_run_id` and
`fork_kind`, so evidence, metrics and policy remain identical to the customer
runtime.

## 9. Explicitly rejected architectures

The following are prohibited:

- a second Agent loop or composite backend;
- a second Tool registry/executor or aliases around retired Tools;
- runtime Tool permissions discovered directly from an MCP Server;
- arbitrary Python plugins, shell/HTTP hooks or auto-updating extensions;
- YOLO/always-approve write execution;
- file-based production Persona, Playbook, Model or Runtime configuration;
- customer long-term memory in the Agent control plane;
- raw Prompt, hidden reasoning or PII persistence;
- background Specialist processes or unjoined tasks;
- replay against a different Release while claiming equivalence.

## 10. Extension rule

New runtime capabilities must extend these authorities in place. Any proposal
that requires a parallel control surface, persistence model, execution path or
compatibility chain must first prove why the canonical authority cannot support
the requirement. Duplication for delivery speed is not an accepted reason.
