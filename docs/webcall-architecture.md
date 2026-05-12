# WebCall Architecture

## Definition

WebCall is the browser voice inbound channel for NexusDesk.

```text
WebChat = text inbound
WebCall = voice inbound
```

Both channels enter the same NexusDesk Ticket Runtime instead of creating isolated products.

## Shared runtime

The shared runtime includes:

```text
conversation -> ticket -> customer context -> agent -> events -> evidence -> AI assist
```

WebCall must use this runtime so the operation can track one customer issue through the same ticket record whether the customer entered by text or by browser voice.

## Current compatibility layer

Current `main` still uses legacy names and paths:

```text
webchat_voice
/api/webchat/.../voice/...
/webchat/voice/{voice_session_id}
```

This is acceptable in the short term. The architecture name is WebCall Inbound, but broad code renaming is intentionally deferred to avoid breaking the current mock foundation.

## Target flow

```text
visitor opens WebCall
  -> NexusDesk initializes or binds conversation and ticket
  -> NexusDesk creates voice session
  -> provider creates or resolves media room
  -> NexusDesk signs visitor participant token
  -> visitor joins WebRTC room
  -> agent sees Incoming WebCall in NexusDesk
  -> agent accepts through NexusDesk endpoint
  -> NexusDesk writes accepted_by_user_id and first-agent-wins state
  -> NexusDesk signs agent participant token
  -> agent joins WebRTC room
  -> call ends
  -> NexusDesk writes events and final ticket evidence
```

## Provider principle

LiveKit is the first real media provider because Phase 1 uses a free self-hosted route.

LiveKit owns media transport:

```text
room, participant token, media connection, lifecycle webhook signal
```

NexusDesk remains the system of record for:

```text
conversation, ticket, accepted_by_user_id, state transitions, timeline, evidence, audit
```

## Phase 1 scope

Phase 1 introduces backend provider capability only:

- `WEBCHAT_VOICE_PROVIDER=mock` remains default and continues to work.
- `WEBCHAT_VOICE_PROVIDER=livekit` no longer fails with provider unavailable when configured correctly.
- NexusDesk can create or resolve a LiveKit room.
- NexusDesk can issue visitor and agent participant tokens scoped to one room.
- Existing conversation/ticket binding, event writes, and `voice_call` evidence behavior remain compatible.

Phase 1 must not implement:

- SIP, PSTN, phone numbers, or CPaaS providers.
- Recording.
- Realtime transcription.
- AI voice.
- Formal inbox panel changes.
- Large API path rename.

## Self-hosted deployment direction

The first deployment route is self-hosted LiveKit.

```text
dev/local: LiveKit dev mode or docker compose
staging: voice subdomain, trusted SSL, WSS endpoint, Nginx websocket proxy, TURN/STUN check
production: independent voice domain, observability, firewall rules, scaling plan
```

External customer browsers must reach a public HTTPS/WSS voice domain. Internal NexusDesk services may continue to use private networking, but WebCall media needs a public browser-reachable endpoint.

Target media endpoint pattern:

```text
voice.<our-domain>
wss://voice.<our-domain>
```

## Later phases

```text
Phase 2: Visitor WebCall room page with click-to-join microphone request
Phase 3: Agent WebCall panel embedded in the formal NexusDesk inbox
Phase 4: lifecycle webhook, missed calls, cleanup worker, metrics
Phase 5: compliance-gated recording, transcription, summary, QA
Phase 6: SIP / PSTN only if hotline business requires it
```
