# WebChat Voice Runtime — Mock UI Phase

## Scope

This document covers PR 3 of the WebChat Voice foundation work.

The goal is to make the WebChat Voice business state visible and operable before connecting any real media provider.

This phase adds:

- An optional public voice entry script: `/webchat/voice-entry.js`
- Demo page integration for that script
- Admin mock console route: `/webchat-voice`
- Frontend voice API client and types
- Static guard tests that prevent accidental WebRTC/LiveKit/microphone behavior in this phase

## Explicit non-goals

This phase does not implement:

- LiveKit real audio
- WebRTC SDK loading
- Browser microphone prompt
- Realtime transcription
- Call recording
- AI voice agent
- SIP, phone numbers, PBX, or PSTN
- Audio relay through FastAPI

## Public customer flow

When `WEBCHAT_VOICE_ENABLED=true`, `/webchat/voice-entry.js` displays a small orange voice button on the demo/customer page.

On click:

1. It ensures a WebChat conversation exists by calling `/api/webchat/init` if needed.
2. It creates or returns the active voice session through:

   ```text
   POST /api/webchat/conversations/{conversation_id}/voice/sessions
   ```

3. It opens the dedicated voice page:

   ```text
   /webchat/voice/{voice_session_id}
   ```

4. It does not request microphone access.
5. It does not load LiveKit or any WebRTC SDK.

## Admin mock console flow

Admins can open:

```text
/webchat-voice
```

The page lists existing WebChat conversations and voice sessions for the selected ticket.

The admin can:

- See a ringing mock voice call
- Accept the mock call
- End the mock call
- Verify that the final `voice_call` message is written into the WebChat thread by the backend service

## Why this route is separate from `/webchat`

The existing `/webchat` page is the production WebChat inbox. This phase keeps the mock voice console separate so the current inbox is not destabilized while the voice runtime is still foundation-only.

Later, after PR 3 is validated, the incoming voice panel can be embedded directly into `/webchat`.

## Feature flag behavior

The public voice entry checks:

```text
GET /api/webchat/voice/runtime-config
```

When voice is disabled, the orange voice button stays hidden.

## Guardrails

Static tests assert that the public voice entry:

- Does not contain `getUserMedia`
- Does not contain `LiveKit`
- Does not contain `RTCPeerConnection`
- Does not contain `MediaRecorder`
- Does call the WebChat voice session API
- Does open the dedicated voice page

These guardrails keep PR 3 within the mock UI state-machine scope.

## Next phase

PR 4 can start only after the infrastructure decisions are made:

- LiveKit self-hosted or managed RTC provider
- Voice domain
- HTTPS/WSS strategy
- TURN strategy
- Nginx websocket proxy strategy
- Secret management for LiveKit API keys

PR 4 should replace the mock provider token with a real media provider token but still avoid realtime transcription and AI voice handling.
