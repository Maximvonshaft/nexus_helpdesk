# WebCall Current State

## Product definition

NexusDesk has two external inbound entry points:

1. **WebChat** — text inbound from an external website widget.
2. **WebCall** — browser-based voice inbound from an external website call entry.

Both channels enter the same NexusDesk Ticket Runtime:

```text
conversation / ticket / customer / agent / event / evidence / AI assist
```

WebCall is not a standalone LiveKit demo and should not be treated as a decorative button inside WebChat. It is NexusDesk's second inbound channel. The current implementation temporarily reuses the existing WebChat conversation and ticket foundation so voice calls can bind to the same operational record.

## Current main branch state

The current `main` branch contains the WebCall mock foundation, implemented under legacy `webchat_voice` naming:

- WebChat Voice business state machine.
- Mock voice provider.
- Voice session database tables.
- Security header and CSP readiness for future browser microphone/WebRTC usage.
- Visitor-side optional voice entry script.
- Admin mock console.
- End-of-call evidence written into the WebChat thread as `message_type=voice_call`.

The current implementation is **not** production real voice:

- No real browser audio transport.
- No LiveKit room connection yet.
- No `getUserMedia()` runtime yet.
- No SIP, PSTN, phone numbers, PBX, Twilio, Vonage, or CPaaS dependency.
- No recording.
- No realtime transcription.
- No AI voice agent.

## Naming decision

The long-term product and architecture name is:

```text
WebCall Inbound
```

The short-term code path may remain:

```text
/api/webchat/.../voice/...
/webchat/voice/{voice_session_id}
backend/app/services/webchat_voice_service.py
```

This preserves API compatibility and avoids risky broad rename work while the real voice provider is being introduced.

## Strategic intent

The goal is not to build a LiveKit sample app. The goal is to plug WebCall into the NexusDesk Ticket Runtime:

```text
External WebCall click
  -> create or bind conversation + ticket
  -> create voice session
  -> establish real WebRTC room
  -> visitor joins
  -> agent accepts in NexusDesk
  -> call ends
  -> ticket timeline / event / evidence is written
```

## Near-term phases

```text
Phase 1: WebCall backend LiveKit provider
Phase 2: Visitor WebCall room page
Phase 3: Agent inbox WebCall panel
Phase 4: lifecycle webhook / missed call / metrics
Phase 5: transcript / summary / QA / compliance
Phase 6: SIP / phone number only if business really needs it
```

## Phase boundary

PR-0 is documentation-only. It does not rename code, change API paths, modify database migrations, or introduce LiveKit runtime behavior.
