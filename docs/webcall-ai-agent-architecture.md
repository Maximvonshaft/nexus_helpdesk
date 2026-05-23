# WebCall AI Agent Architecture

## Scope

PR-0/PR-1 does not make WebCall AI functional yet. PR-0/PR-2 does not make WebCall AI functional yet. It only adds the guarded architecture, schema, config, tests, and no-op worker claim lifecycle. No real AI voice, STT, TTS, LiveKit AI worker media join, or Speedaf write automation is introduced here.

The target product is WebCall AI Front Desk: a customer starts a WebCall, an AI voice agent joins as the first support participant, asks for tracking information and caller confirmation, lets NexusDesk check trusted Speedaf facts, answers low-risk tracking questions, and hands complex or high-risk cases to a human agent.

## Runtime Boundary

WebCall remains the voice channel. LiveKit remains the real-time media room. PR-2 adds only a backend AI worker claim lifecycle skeleton: eligible sessions can be claimed, heartbeated, released, or failed with a lease, but the worker does not join media. In a later PR, a backend AI worker will join the LiveKit room as an AI participant. NexusDesk remains the control plane and system of record for state, facts, action decisions, audit, evidence, and human handoff.

The intended flow is:

```text
Customer WebCall
  -> LiveKit room
  -> backend AI worker joins as AI participant
  -> AI speaks and classifies intent
  -> NexusDesk checks trusted facts and action policy
  -> low-risk answer or human handoff
  -> auditable turn/action records
```

AI may speak and classify. NexusDesk decides and executes.

```text
Model output -> NexusDesk Action Gate -> allowed | blocked | handoff | failed
```

## Security Boundaries

Browser code must never receive AI provider tokens, OpenClaw tokens, Speedaf appCode, Speedaf secretKey, signing material, full phone numbers, full addresses, or raw Speedaf payloads.

The LLM must never directly execute Speedaf write actions. These direct actions are forbidden:

```text
speedaf.order.cancel
speedaf.order.update_address
speedaf.work_order.create
```

Speedaf MCP access stays backend-governed. Future write behavior must pass through NexusDesk Action Gate, ToolCallLog, TicketEvent, BackgroundJob, allowlists, idempotency, and human handoff rules as applicable.

## V1 Foundation Actions

These actions are allowed as schema concepts in the foundation only:

```text
ask_tracking_number
ask_caller_confirmation
lookup_tracking
ask_waybill_suffix_selection
explain_tracking_fact
request_delivery_followup
handoff_to_human
end_call
```

`request_delivery_followup` is only an intake/request concept in PR-1. It is not executable Speedaf work-order behavior.

## Forbidden And Handoff Scope

The following remain forbidden for AI automation and require human handoff or explicit future approval:

```text
cancel automation
address update confirmation
compensation or refund promises
driver or DSP responsibility judgment
customs or payment disputes
legal or privacy questions
low confidence outcomes
unknown language beyond enabled support
```

The AI may gather context and route the case, but it must not confirm cancellation, submit address changes directly, promise compensation, promise delivery times, blame drivers or DSPs, contact drivers or DSPs directly, or execute Speedaf writes directly.

## Data Model

`webchat_voice_sessions` receives AI lifecycle metadata: status, start/end timestamps, handoff reason, language, and turn count.

PR-2 extends `webchat_voice_sessions` with worker claim metadata: worker id, claimed timestamp, lease expiration, last heartbeat, and error code/message. The PR-2 status vocabulary is limited to `pending`, `claimed`, `released`, `failed`, and `skipped`; it does not introduce media states such as joined, speaking, or listening.

`webchat_voice_ai_turns` stores redacted AI conversation turns only. It must not store raw unredacted customer speech. Raw/final transcript storage remains the responsibility of transcript segment tables and later redaction pipelines.

`webchat_voice_ai_actions` records model-requested actions and NexusDesk decisions. `tool_call_log_id` is an indexed nullable integer without a foreign key in this foundation PR to keep audit linkage low-coupling and avoid cross-module migration coupling.

## Rollout Path

1. PR-0/PR-1: guarded architecture, config, schema, data model, and tests.
2. PR-2: webcall-ai-worker skeleton and AI session claim lifecycle only; this is a no-op claim lifecycle only and does not connect media, STT, TTS, LLM, or Speedaf.
3. Mock STT/TTS integration with deterministic fixtures.
4. Real STT/TTS provider integration behind feature flags.
5. Trusted Speedaf tracking lookup through backend policy.
6. Human handoff workflows and operator evidence.
7. Summary, callback, and evidence hardening.

## Non-Goals

This foundation does not implement real STT. It does not implement real TTS. It also does not implement a real LiveKit AI participant join, real OpenClaw/LLM voice calls, frontend WebCall UI changes, AI handoff UI, or any Speedaf write action execution from AI. This PR does not implement functional AI voice.
