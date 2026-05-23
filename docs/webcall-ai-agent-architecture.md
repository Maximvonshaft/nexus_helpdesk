# WebCall AI Agent Architecture

## Scope

PR-0/PR-1 does not make WebCall AI functional yet. PR-0/PR-2 does not make WebCall AI functional yet. PR-3 does not make WebCall AI functional yet. PR-4 does not implement functional AI voice. PR-5 does not implement functional AI voice. PR-6 does not implement functional AI voice. PR-7 does not implement functional AI voice. PR-8 does not implement functional AI voice. PR-9 does not implement functional AI voice. Acceleration Pack A does not implement functional AI voice. Acceleration Pack B does not implement full AI voice. These PRs only add the guarded architecture, schema, config, tests, no-op worker claim lifecycle, deterministic mock turn persistence, deterministic mock STT/TTS boundaries, the real STT/TTS provider contract skeleton, the first Deepgram STT adapter behind feature flags, a controlled static HTTPS audio reference source for STT input, a fake LiveKit AI participant ownership skeleton, a server-side LiveKit AI participant token issuer wrapper, a backend no-media AI presence runtime, and controlled audio ingress plus STT transcript persistence. No WebRTC audio subscription by default, AI audio publish path, TTS provider, LLM/runtime integration, or Speedaf write automation is introduced here.

The target product is WebCall AI Front Desk: a customer starts a WebCall, an AI voice agent joins as the first support participant, asks for tracking information and caller confirmation, lets NexusDesk check trusted Speedaf facts, answers low-risk tracking questions, and hands complex or high-risk cases to a human agent.

## Runtime Boundary

WebCall remains the voice channel. LiveKit remains the real-time media room. PR-2 adds only a backend AI worker claim lifecycle skeleton: eligible sessions can be claimed, heartbeated, released, or failed with a lease, but the worker does not join media. In a later PR, a backend AI worker will join the LiveKit room as an AI participant. NexusDesk remains the control plane and system of record for state, facts, action decisions, audit, evidence, and human handoff.

PR-3 adds deterministic mock turn execution only. A claimed worker-owned session can write one redacted `webchat_voice_ai_turns` row and one safe `webchat_voice_ai_actions` decision row, then release. It does not read audio, publish audio, call STT/TTS, call an LLM/provider, call OpenClaw, or call Speedaf.

PR-4 adds deterministic mock STT/TTS boundaries only. The worker obtains a fixed redacted customer utterance from the mock STT boundary, writes it into the audited AI turn, obtains fixed TTS metadata for the deterministic AI response, and releases the session. PR-4 does not implement functional AI voice. It does not read audio, publish audio, or join LiveKit media. It does not join LiveKit media, does not connect real STT/TTS, does not call LLM/provider runtime, does not call OpenClaw, does not call Speedaf, and does not change frontend. Future real providers must implement the provider interfaces and remain behind feature flags.

PR-5 adds a real STT/TTS provider contract skeleton only. It introduces provider-neutral media schema names, provider routing for `mock`, `disabled`, and `contract_stub`, and fail-closed token-file, timeout, and canary configuration. PR-5 does not implement real STT/TTS. PR-5 does not implement functional AI voice. It does not join LiveKit, does not read/publish real audio, does not import real provider SDKs, does not perform external network calls, does not call LLM/provider runtime, does not call OpenClaw, does not call Speedaf, and does not change frontend. Real provider SDK/network integration is reserved for PR-6 or later.

PR-6 adds the first Deepgram STT adapter only. It is disabled by default, requires explicit `WEBCALL_STT_PROVIDER=deepgram` and `WEBCALL_STT_DEEPGRAM_ENABLED=true`, and uses token-file rules, timeouts, canary config, HTTPS remote audio references, and fake-transport-tested boundaries. PR-6 does not implement functional AI voice. It does not join LiveKit, does not read/publish WebRTC audio, does not change frontend, does not call LLM/provider runtime, does not call OpenClaw, does not call Speedaf, and does not enable real STT by default.

PR-7 adds a controlled `audio_reference` resolver only. It is disabled by default with `WEBCALL_AI_AUDIO_REFERENCE_SOURCE=disabled`. The only non-default source is `static_fixture`, which requires `WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED=true`, an HTTPS `WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL`, optional exact-host `WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST`, and is rejected in production. PR-7 does not implement functional AI voice. It does not join LiveKit, does not read/publish WebRTC audio, does not upload or read audio files, does not change frontend, does not call LLM/provider runtime/OpenClaw/OpenAI/Codex, does not call Speedaf, and does not enable Deepgram by default.

PR-8 adds a fake LiveKit AI participant ownership skeleton only. It can create an AI participant identity and DB participant record, issue a fake token, perform fake join/leave ownership transitions, run the existing mock turn path, and mark the participant left. It is disabled by default with `WEBCALL_AI_PARTICIPANT_ENABLED=false`, supports only `WEBCALL_AI_PARTICIPANT_MODE=fake_room_client`, and rejects participant enablement in production. PR-8 does not implement functional AI voice. It does not join LiveKit media, subscribe/publish audio, read WebRTC tracks, change frontend, call LLM/provider runtime/OpenClaw/OpenAI/Codex, call Speedaf, execute Speedaf writes, or expose AI participant tokens to browsers.

PR-9 adds a server-side LiveKit AI participant token issuer wrapper only. It extends participant mode to `fake_room_client | livekit_token_issuer`, requires `WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED=true` for token issuer mode, and rejects token issuer mode in production. The wrapper can call the existing backend `VoiceProvider.issue_participant_token` boundary and then performs no-media join/leave state transitions only. PR-9 does not implement functional AI voice. It does not join LiveKit media, subscribe/publish audio, read WebRTC tracks, change frontend, call LLM/provider runtime/OpenClaw/OpenAI/Codex, call Speedaf, execute Speedaf writes, persist participant tokens, log participant tokens, or expose AI participant tokens to browsers.

Acceleration Pack A adds an AI no-media presence runtime only. It is disabled by default with `WEBCALL_AI_ROOM_PRESENCE_ENABLED=false`, supports `WEBCALL_AI_ROOM_PRESENCE_MODE=fake_no_media | livekit_no_media`, bounds join timeout with `WEBCALL_AI_ROOM_PRESENCE_JOIN_TIMEOUT_MS`, and rejects presence enablement in production. In fake mode the worker issues a fake AI participant token, marks no-media presence joined, runs the existing mock turn, marks no-media presence left, and releases the session. In LiveKit mode it requires `WEBCALL_AI_PARTICIPANT_ENABLED=true`, `WEBCALL_AI_PARTICIPANT_MODE=livekit_token_issuer`, and `WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED=true`; it issues the server-side AI participant token and attempts a LiveKit room presence connection with no audio publish or subscription. Acceleration Pack A does not implement functional AI voice. It does not subscribe to audio, publish audio, read WebRTC tracks, change frontend, call LLM/provider runtime/OpenClaw/OpenAI/Codex, call Speedaf, execute Speedaf writes, persist participant tokens, log participant tokens, or expose AI participant tokens to browsers.

Acceleration Pack B adds controlled audio ingress plus STT runtime and transcript persistence only. It is disabled by default with `WEBCALL_AI_STT_RUNTIME_ENABLED=false`, supports `WEBCALL_AI_STT_RUNTIME_MODE=mock_text | audio_reference`, and writes `webchat_voice_transcript_segments` only when `WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED=true`. The worker can resolve a controlled static audio reference, run the configured STT provider, persist a redacted final transcript segment idempotently, use the redacted STT text in the existing mock AI turn, and release safely. Acceleration Pack B does not implement full AI voice. It does not query Speedaf, generate or publish AI audio, change frontend, call LLM/provider runtime/OpenClaw/OpenAI/Codex, execute Speedaf writes, persist tokens, log tokens, or expose AI participant tokens/transcripts to browsers.

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

Acceleration Pack B writes final customer transcript segments into `webchat_voice_transcript_segments` only behind explicit STT runtime and transcript write flags. It stores redacted text in both `text_raw` and `text_redacted` for this controlled milestone, marks `redaction_status=redacted`, and keeps provider payloads, raw audio, tokens, and secrets out of persistence.

`webchat_voice_ai_actions` records model-requested actions and NexusDesk decisions. `tool_call_log_id` is an indexed nullable integer without a foreign key in this foundation PR to keep audit linkage low-coupling and avoid cross-module migration coupling.

## Rollout Path

1. PR-0/PR-1: guarded architecture, config, schema, data model, and tests.
2. PR-2: webcall-ai-worker skeleton and AI session claim lifecycle only; this is a no-op claim lifecycle only and does not connect media, STT, TTS, LLM, or Speedaf.
3. PR-3: deterministic mock turn execution only, writing auditable mock turn/action rows with no external runtime.
4. PR-4: deterministic mock STT/TTS boundaries only, with no audio, STT, TTS, LLM, OpenClaw, or Speedaf calls to real providers.
5. PR-5: real STT/TTS provider contract skeleton and fail-closed provider router only, with no SDK or network integration.
6. PR-6: Deepgram pre-recorded STT adapter behind feature flags, fake transport tests, and HTTPS remote audio reference controls.
7. PR-7: controlled static HTTPS audio reference resolver wired into STT input, disabled by default and rejected in production.
8. PR-8: fake LiveKit AI participant ownership skeleton with deterministic identity, fake token, and fake join/leave state transitions, disabled by default and rejected in production.
9. PR-9: server-side LiveKit AI participant token issuer wrapper, explicitly enabled only, no media join, no token persistence, and rejected in production.
10. Acceleration Pack A: backend AI no-media presence runtime, disabled by default and rejected in production.
11. Acceleration Pack B: controlled audio ingress plus STT runtime and transcript persistence, disabled by default and rejected in production.
12. TTS provider integration behind feature flags and canary controls.
13. Trusted Speedaf tracking lookup through backend policy.
14. Human handoff workflows and operator evidence.
15. Summary, callback, and evidence hardening.

## Non-Goals

This foundation does not implement full AI voice. It does not implement real TTS. It also does not implement production LiveKit audio subscription by default, real OpenClaw/LLM voice calls, frontend WebCall UI changes, AI handoff UI, or any Speedaf write action execution from AI.
