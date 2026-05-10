# WebChat Voice Readiness & Security Header Gate

## Scope

This document covers the first WebChat Voice foundation step only.

This phase prepares NexusDesk for a future pure internet-based WebChat voice call runtime. It does not implement real media, LiveKit, realtime transcription, recording, SIP, phone numbers, PBX, or AI voice handling.

## Why this phase exists

The existing application is intentionally secure by default. The global HTTP middleware sets browser security headers that deny microphone access and restrict outbound browser connections. That is correct for the current text WebChat runtime, but it blocks future WebRTC voice pages.

The readiness gate introduces a path-scoped exception model:

- All normal pages continue to deny microphone access.
- Camera remains denied.
- Geolocation remains denied.
- Voice headers are only applied when `WEBCHAT_VOICE_ENABLED=true` and the request path matches `WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES`.
- Additional realtime connection targets must be explicitly configured in `WEBCHAT_VOICE_CONNECT_SRC`.
- Wildcard connect sources are rejected.

## Added runtime configuration

| Setting | Default | Purpose |
|---|---:|---|
| `WEBCHAT_VOICE_ENABLED` | `false` | Global feature flag for voice readiness headers and placeholder page. |
| `WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES` | `/webchat/voice` | Comma-separated path prefixes allowed to receive voice-specific headers. |
| `WEBCHAT_VOICE_CONNECT_SRC` | empty | Space/comma-separated HTTPS/WSS connect-src entries for future voice transport. |
| `WEBCHAT_VOICE_PROVIDER` | `mock` | Provider placeholder. Only `mock` and `livekit` are valid. |
| `WEBCHAT_VOICE_SESSION_TTL_SECONDS` | `900` | Future short-lived voice session/token TTL. |
| `WEBCHAT_VOICE_MAX_ACTIVE_PER_CONVERSATION` | `1` | Future active-session guardrail per WebChat conversation. |
| `WEBCHAT_VOICE_RATE_LIMIT_WINDOW_SECONDS` | `60` | Future voice-specific create-session rate-limit window. |
| `WEBCHAT_VOICE_RATE_LIMIT_MAX_REQUESTS` | `5` | Future voice-specific create-session rate-limit count. |
| `WEBCHAT_VOICE_RECORDING_ENABLED` | `false` | Recording remains disabled in this phase. |
| `WEBCHAT_VOICE_TRANSCRIPTION_ENABLED` | `false` | Realtime transcription remains disabled in this phase. |

## Header behavior

### Default paths

Default response headers remain strict:

```text
Permissions-Policy: camera=(), microphone=(), geolocation=()
Content-Security-Policy: default-src 'self'; ...; connect-src 'self'; ...
```

### Voice path when enabled

For allowed voice paths only:

```text
Permissions-Policy: camera=(), microphone=(self), geolocation=()
Content-Security-Policy: default-src 'self'; ...; connect-src 'self' <WEBCHAT_VOICE_CONNECT_SRC>; ...
```

Camera and geolocation are not enabled for the MVP.

## Dynamic route ordering

The placeholder route is registered at:

```text
/webchat/voice/{voice_session_id}
```

It is registered before the existing `/webchat` static mount so the future voice page is not swallowed by `StaticFiles`.

If the project later confirms a route conflict, the reserved fallback route is:

```text
/voice/webchat/{voice_session_id}
```

## Explicit non-goals in this phase

This phase deliberately excludes:

- LiveKit real audio.
- WebRTC SDK loading.
- Browser microphone prompt on page load.
- Realtime transcription.
- Call recording.
- AI voice agent speaking to the customer.
- SIP, PBX, PSTN, phone numbers, or SIM cards.
- Audio relay through FastAPI.
- Audio processing inside the existing polling worker.

## Rollback

Rollback is feature-flag first:

```text
WEBCHAT_VOICE_ENABLED=false
```

With the feature disabled, voice paths keep the default strict microphone-denied headers and the placeholder route returns disabled/not-found behavior.

No database migration or persistent data is introduced in this phase.

## Next phase

The next implementation phase should add the durable business foundation:

- `webchat_voice_sessions`
- `webchat_voice_participants`
- `webchat_voice_transcript_segments`
- Public create/end APIs using `X-Webchat-Visitor-Token`
- Admin list/accept/end APIs with `ensure_ticket_visible`
- Mock provider contract
- WebchatEvent lifecycle writes

Real media, transcription, recording, and AI voice must remain out of scope until the foundation and mock UI are complete.
