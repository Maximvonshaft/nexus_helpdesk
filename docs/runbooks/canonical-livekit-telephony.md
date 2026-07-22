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
| Channel configuration | `ChannelAccount(provider="voice")` plus `VoiceChannelConfiguration` |
| Provider events | `TelephonyEventInbox` |
| Provider commands | durable `WebchatVoiceSessionAction` outbox |
| Business follow-up | optional `Ticket` |

There is no second VoiceQueue, AgentPresence, VoiceConversation, call owner or telephony administration product.

## Production prerequisites

Real PSTN activation requires all of the following external facts:

1. A LiveKit deployment or LiveKit Cloud project with SIP support.
2. A carrier/SIP provider and at least one DID.
3. Inbound and, when outbound calls are required, outbound SIP trunks.
4. A LiveKit SIP dispatch rule for every enabled inbound number.
5. A deployed LiveKit Room controller/AI Agent.
6. Valid webhook and Agent/controller credentials.
7. Network, firewall, DNS and certificate configuration required by the chosen deployment.
8. Approved recording, transcription, retention and customer-consent policy for every operating market.

Without these credentials Nexus can deploy the code and configuration control plane, but it must return an auditable unavailable/error state. Mock provider success is prohibited in production.

## Runtime configuration

Use secret files in production rather than inline secret values.

```text
WEBCHAT_HUMAN_CALL_ENABLED=true
WEBCHAT_LIVE_AI_VOICE_ENABLED=true
WEBCHAT_VOICE_PROVIDER=livekit
WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES=/webchat,/api/webchat,/api/telephony
WEBCHAT_VOICE_CONNECT_SRC=wss://<livekit-host>
LIVEKIT_URL=wss://<livekit-host>
LIVEKIT_API_KEY_FILE=/run/secrets/livekit_api_key
LIVEKIT_API_SECRET_FILE=/run/secrets/livekit_api_secret
LIVEKIT_AGENT_NAME=<deployed-controller-agent-name>
LIVEKIT_AGENT_SHARED_SECRET_FILE=/run/secrets/livekit_agent_shared_secret
LIVEKIT_WEBHOOK_ENABLED=true
```

`LIVEKIT_URL` must use `wss://` in production. The LiveKit WebSocket origin must be present in the controlled CSP/connect-src configuration.

## Configure a phone number

1. Open **Channels → Phone and real-time voice**.
2. Select the existing `ChannelAccount(provider="voice")` for the DID.
3. Configure operational policy:
   - AI-first or human-first answering.
   - Number timezone and business hours.
   - Maximum customer wait.
   - Per-agent ringing timeout.
   - After-call work time.
   - Overflow: continue AI, voicemail, or explain and disconnect.
   - Recording and transcription policy.
4. In **Advanced Provider diagnostics**, configure:
   - LiveKit project reference.
   - Inbound trunk ID.
   - Outbound trunk ID when callback/outbound calling is required.
   - SIP dispatch rule ID.
   - Deployed Room controller/AI Agent name.
5. Enable the number only after required Provider references are present.

Tenant is never accepted from the browser or Provider payload. Nexus resolves Tenant and ChannelAccount from the server-managed DID, trunk or dispatch-rule mapping.

## Configure operators

Every operator who can receive a voice call must have:

- Active user account.
- Existing queue scope grant for the exact tenant, country and `voice` channel.
- `OperatorAgentState.status=online`.
- Fresh heartbeat within the configured TTL.
- `voice_enabled=true`.
- Governed `max_concurrent_voice_calls` and after-call work duration.

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
→ Room controller/AI Agent dispatch
→ Agent Runtime
```

For AI-first answering, the Agent Runtime receives transcribed customer turns and uses the same governed knowledge, Tools and policies as other channels.

## Adaptive human handoff

The approved `agent.playbook.human-handoff` requires this sequence:

1. Call `support.availability` before promising a transfer.
2. Use only its committed observation for eligible capacity, queue position and wait estimate.
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
- AI audio is muted on human takeover, while the Room controller remains available for call controls.

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

Hold, resume, DTMF and warm transfer wait for a joined Room controller and a signed acknowledgement. Server delivery is not reported as call completion.

## Provider Event Inbox

Every signed event is recorded with:

- Provider event ID and payload digest.
- Safe redacted summary.
- Encrypted replay envelope.
- Raw object-storage evidence.
- Tenant and ChannelAccount projection.
- Attempt count, lease, retry and dead-letter state.

A duplicate ID with a different payload is rejected and audited. Projection uses a savepoint so a transient projection failure does not destroy Inbox evidence. The existing background worker replays retryable or stale events.

## Recording and transcription

Recording and transcription are disabled unless the configured channel policy permits them. System-originated recording commands use the same durable outbox and audit trail as operator commands.

LiveKit egress events update recording status. Storage retention and access control must match the approved market policy before `always` is enabled.

## Failure behavior

| Failure | Required behavior |
| --- | --- |
| Unknown DID/trunk/dispatch rule | Record safe Inbox evidence; do not create Tenant data |
| Invalid webhook signature | Reject before parsing/projection |
| Missing LiveKit credentials | Return explicit unavailable; do not simulate success |
| No eligible operator | Keep Room and AI/wait strategy active; do not assign owner |
| Operator decline/offer expiry | Route the next offer; do not hang up caller |
| Controller not joined | Keep command retryable; do not report execution |
| Provider command failure | Retry when classified transient, otherwise fail with audit evidence |
| Duplicate/out-of-order event | Idempotently project one canonical call |
| Customer hangs up | End call projection and close pending Handoff/offers/tasks |

## Deployment verification

Before release, use the canonical acceptance workflow on the exact PR Head and require:

- Backend production compile and complete pytest suite.
- Static service-authority and telephony residue checks.
- Frontend architecture, lint, TypeScript, tests and production build.
- Playwright browser journeys.
- PostgreSQL `upgrade head → downgrade previous → upgrade head`.
- Image build, Trivy, SBOM, migration and health checks.
- Secret scan, SAST, dependency audit and zero CodeQL findings.

After deploying with real Provider credentials, run a controlled call matrix:

1. Inbound AI-first call.
2. Availability query with free capacity.
3. AI-to-human offer and accept in the same Room.
4. Decline and timeout rotation without customer disconnect.
5. Busy operators with evidence-based wait response.
6. Explicit confirmed follow-up Ticket creation.
7. Hold, resume, DTMF, cold transfer and warm transfer.
8. Customer and authorized operator hangup.
9. Recording/transcription policy and evidence events.
10. Outbound call success, busy, no-answer and Provider failure.

Do not claim production PSTN activation until this matrix has passed using the actual carrier, DID, trunks and webhook configuration.
