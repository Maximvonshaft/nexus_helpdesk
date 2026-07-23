# Canonical LiveKit Telephony Runbook

## Purpose

Nexus uses LiveKit as the only real-time media plane for browser voice and PSTN/SIP. Nexus remains the system of record and orchestration authority for Conversation, Handoff, Agent Runtime, operator presence/capacity, ChannelAccount, evidence and audit.

A phone call does not require a Ticket. A Ticket is created only when a real follow-up is required and the customer confirms the exact proposed action.

## Canonical authorities

| Responsibility | Authority |
| --- | --- |
| Customer interaction | `WebchatConversation` |
| Human ownership | accepted `WebchatHandoffRequest.assigned_agent_id` plus `WebchatConversation.active_agent_id` |
| AI decisions and Tools | governed Agent Release / Agent Runtime |
| Operator availability | `OperatorAgentState` plus queue scope grants |
| Agent ringing | short-lived `VoiceRoutingOffer` |
| Media/call projection | `WebchatVoiceSession` and `WebchatVoiceParticipant` Call Legs |
| Live media adapter | `backend/app/livekit_agent_worker.py` |
| Channel configuration | `ChannelAccount(provider="voice")` plus `VoiceChannelConfiguration` |
| Provider events | `TelephonyEventInbox` |
| Provider commands | durable `WebchatVoiceSessionAction` outbox |
| Business follow-up | optional `Ticket` |

The LiveKit Agent worker is not a second AI Runtime. It performs room participation, STT, TTS, DTMF and bounded room control. Every customer turn is sent to `/api/telephony/internal/agent-turn`, where the existing governed Nexus Agent Runtime performs reasoning and Tool execution.

There is no second VoiceQueue, AgentPresence, VoiceConversation, call owner, business LLM or telephony administration product.

## Production prerequisites

Real PSTN activation requires all of the following external facts:

1. A LiveKit deployment or LiveKit Cloud project with SIP support.
2. A carrier/SIP provider and at least one DID.
3. Inbound and, when outbound calls are required, outbound SIP trunks.
4. A LiveKit SIP dispatch rule for every enabled inbound number.
5. A running `livekit-agent-controlled` media worker registered under the configured Agent name.
6. Valid STT and TTS model identifiers available through LiveKit Inference or the selected provider integration.
7. Valid webhook and Agent/controller credentials.
8. Network, firewall, DNS and certificate configuration required by the chosen deployment.
9. Approved recording, transcription, retention and customer-consent policy for every operating market.

Without these credentials Nexus can deploy the code and configuration control plane, but it must return an auditable unavailable/error state. Mock provider success is prohibited in production.

## Runtime configuration

Use secret files in production rather than inline secret values.

```text
WEBCHAT_HUMAN_CALL_ENABLED=true
WEBCHAT_LIVE_AI_VOICE_ENABLED=true
WEBCHAT_VOICE_PROVIDER=livekit
WEBCHAT_VOICE_ROUTING_MODE=ai_first
WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES=/webcall
WEBCHAT_VOICE_CONNECT_SRC=wss://<livekit-host>
LIVEKIT_URL=wss://<livekit-host>
LIVEKIT_API_KEY_FILE=/run/secrets/livekit_api_key
LIVEKIT_API_SECRET_FILE=/run/secrets/livekit_api_secret
LIVEKIT_AGENT_NAME=nexus-voice-agent
LIVEKIT_AGENT_SHARED_SECRET_FILE=/run/secrets/livekit_agent_shared_secret
LIVEKIT_WEBHOOK_ENABLED=true
NEXUS_VOICE_STT_MODEL=<livekit-inference-stt-model>
NEXUS_VOICE_TTS_MODEL=<livekit-inference-tts-model-and-voice>
NEXUS_VOICE_TURN_DETECTION=stt
NEXUS_VOICE_AGENT_REQUEST_TIMEOUT_SECONDS=30
NEXUS_VOICE_AGENT_HEARTBEAT_SECONDS=30
```

`LIVEKIT_URL` must use `wss://` in production. The LiveKit WebSocket origin must be present in the controlled CSP/connect-src configuration.

Warm transfer uses the same joined Room controller and no additional LLM. The controller creates one consultation SIP leg, keeps the customer isolated on hold while the current human operator briefs the target, and completes or cancels only through separate durable commands and Provider acknowledgements.

## Controlled deployment

Deploy the normal controlled topology first. Do not enable telephony merely by setting Web flags.

```bash
docker compose \
  --env-file deploy/.env.controlled \
  -f deploy/docker-compose.controlled.yml \
  up -d migrate-controlled app-controlled worker-background-controlled
```

After the LiveKit project, Carrier, DID, trunks, dispatch rule, webhook, STT/TTS and Agent credentials are complete, start the canonical media worker through the existing controlled Compose product:

```bash
docker compose \
  --env-file deploy/.env.controlled \
  -f deploy/docker-compose.controlled.yml \
  --profile telephony \
  up -d livekit-agent-controlled
```

The AgentServer exposes its readiness endpoint at `http://127.0.0.1:8081/` inside the container. It returns success only when the Agent server is connected and operating. The Compose health check uses this endpoint; process-name or `/proc` probes are forbidden.

The Web process and Agent worker must use the same `LIVEKIT_AGENT_SHARED_SECRET`. The worker authenticates the internal Agent-turn request through the Authorization header and signs controller events with timestamped HMAC. Secret values must never appear in logs, examples or evidence bundles.

## Configure a phone number

1. Open **Channels → Phone and real-time voice**.
2. Select the existing `ChannelAccount(provider="voice")` for the DID.
3. Configure operational policy:
   - AI-first or human-first answering.
   - Number timezone and business hours.
   - Maximum customer wait.
   - Per-agent ringing timeout.
   - After-call work time.
   - Overflow: continue with the canonical AI runtime when ready, or explain and disconnect.
   - Recording and transcription policy.
4. In **Advanced Provider diagnostics**, configure:
   - LiveKit project reference.
   - Inbound trunk ID.
   - Outbound trunk ID when callback/outbound calling is required.
   - SIP dispatch rule ID.
   - Deployed Room controller/AI Agent name.
5. Enable the number only after required Provider references are present and the media worker health check is green.

Tenant is never accepted from the browser or Provider payload. Nexus resolves Tenant and ChannelAccount from the server-managed DID, trunk or dispatch-rule mapping.

## Configure operators

Every operator who can receive a voice call must have:

- Active user account.
- Existing queue scope grant for the exact tenant, country and `voice` channel.
- `OperatorAgentState.status=online`.
- Fresh heartbeat within the configured TTL.
- `voice_enabled=true`.
- One-call Voice capacity and governed after-call work duration.

The availability Tool counts active accepted Handoffs and unexpired VoiceRoutingOffer reservations. It does not use a second telephony presence table.

## Inbound call flow

```text
Carrier/DID
→ LiveKit SIP trunk and dispatch rule
→ canonical LiveKit Room
→ signed LiveKit webhook
→ TelephonyEventInbox
→ server DID/trunk/dispatch Tenant mapping
→ Conversation and Voice Session projection
→ named LiveKit Agent worker dispatch
→ STT transcript
→ authenticated internal Agent-turn API
→ governed Agent Runtime and Tools
→ TTS in the same Room
```

For AI-first answering, the Agent Runtime receives transcribed customer turns and uses the same governed knowledge, Tools and policies as other channels. The media worker does not independently decide whether to transfer, create a Ticket or call a business integration.

## Adaptive human handoff

The approved `agent.playbook.human-handoff` requires this sequence:

1. Call `support.availability` before promising a transfer.
2. Use only its committed observation for eligible voice capacity, queue position and wait estimate.
3. If capacity is available and transfer is appropriate, call `handoff.request.create`.
4. If all eligible operators are busy, explain the evidence-based range and confidence, then ask whether the customer wants to wait, continue with AI, or request follow-up.
5. Create a Ticket only for a real follow-up and only after the customer confirms the exact proposal.

Wait estimates use recent completed voice service durations in the same tenant/country/channel scope. When there are fewer than the required samples, the Tool returns no estimate; the Agent must not invent one.

## Customer confirmation

Confirmation-required Tools use a server-side one-time grant bound to:

- Conversation.
- Tenant.
- Tool name.
- Exact canonical argument digest.
- Expiry time.

Ambiguous replies do not grant execution. A denial closes the challenge. Successful execution consumes the grant. Replays or changed arguments require a new confirmation. A model-supplied boolean is never trusted as customer consent.

## Human ringing and acceptance

`VoiceRoutingOffer` represents one agent-level ringing attempt. It is not ownership.

- Offer decline or timeout closes only that offer and routes the next eligible operator.
- The customer Room remains open.
- Handoff Assignment and `Conversation.active_agent_id` are written only after explicit accept under transaction locks.
- Concurrent accepts resolve to one winner.
- Human takeover durably suspends the AI turn path before further Tool or customer-visible side effects, while the Room controller remains available for call controls.

## Durable call controls

The operator API writes a durable command. The background worker leases and dispatches it.

```text
API
→ WebchatVoiceSessionAction
→ canonical background worker
→ LiveKit Server API or joined Room controller
→ Provider/controller event
→ TelephonyEventInbox
→ command and call projection
→ Timeline/AdminAudit
```

Hold, resume, DTMF and every warm-consultation phase wait for a joined Room controller and a signed acknowledgement. Server delivery is not reported as call completion. A consultation target answering means only that consultation began; transfer completion requires the explicit `warm_transfer_complete` command.

## Provider Event Inbox

Every signed event is recorded with:

- Provider event ID and payload digest.
- Minimal safe redacted summary.
- One authenticated encrypted replay envelope.
- Tenant and ChannelAccount projection.
- Attempt count, lease, retry and dead-letter state.

The raw Provider body is not duplicated into object storage. A duplicate ID with a different payload is rejected and audited. Projection uses a savepoint so a transient projection failure does not destroy Inbox evidence. The existing background worker replays retryable or stale events.

## Recording and transcription

Recording and transcription remain disabled unless the configured channel policy, required customer notice or consent, scoped access control, retention and deletion policy are all active. System-originated recording commands use the same durable outbox and audit trail as operator commands.

LiveKit Egress events update recording status. Enabling recording does not itself prove notice or consent; the approved runtime sequence must complete before recording starts.

## Failure behavior

| Failure | Required behavior |
| --- | --- |
| Unknown DID/trunk/dispatch rule | Record safe Inbox evidence; do not create Tenant data |
| Invalid webhook signature | Reject before parsing/projection |
| Missing LiveKit/STT/TTS credentials | Media worker fails readiness; do not simulate success |
| Media worker not registered | Agent dispatch cannot become active; surface unavailable diagnostics |
| No eligible operator | Keep Room and AI/wait strategy active; do not assign owner |
| Operator decline/offer expiry | Route the next offer; do not hang up caller |
| Controller not joined | Keep command retryable; do not report execution |
| Consultation target fails or leaves | Remove the consult leg, restore the original customer/operator media path, and keep the customer call active |
| Provider command failure | Retry when classified transient, otherwise fail with audit evidence |
| Duplicate/out-of-order event | Idempotently project one canonical call |
| Customer hangs up | End call projection and close pending Handoff/offers/tasks |

## Deployment verification

Before release, use the canonical acceptance workflow on the exact PR Head and require:

- Backend production compile and complete pytest suite, including media-worker tests.
- Static service-authority and telephony residue checks.
- Frontend architecture, lint, TypeScript, tests and production build.
- Playwright browser journeys.
- PostgreSQL `upgrade head → downgrade previous → upgrade head`.
- Image build, Trivy, SBOM, migration and health checks.
- Secret scan, SAST, dependency audit and zero CodeQL findings.

After deploying with real Provider credentials, run a controlled call matrix:

1. Media worker readiness and named Agent registration.
2. Inbound AI-first call with real STT and TTS.
3. Availability query with free capacity.
4. AI-to-human offer and accept in the same Room.
5. Decline and timeout rotation without customer disconnect.
6. Busy operators with evidence-based wait response.
7. Explicit confirmed follow-up Ticket creation.
8. Hold, resume, DTMF, cold transfer and warm consultation start/complete/cancel.
9. Customer and authorized operator hangup.
10. Recording/transcription policy and evidence events.
11. Outbound call success, busy, no-answer and Provider failure.

Do not claim production PSTN activation until this matrix has passed using the actual carrier, DID, trunks, models, Agent worker and webhook configuration.
