# WebCall LiveKit Provider Runbook

## Scope

This runbook covers Phase 1 only: backend LiveKit provider support for WebCall Inbound.

Phase 1 lets NexusDesk create or resolve a LiveKit room and issue room-scoped participant tokens for visitors and agents. It does not implement the browser room page, the formal agent inbox panel, SIP, PSTN, recording, transcription, lifecycle webhooks, or AI voice.

## Runtime definition

WebCall is the browser voice inbound channel for NexusDesk. LiveKit provides media transport only. NexusDesk remains the system of record for conversation, ticket, agent ownership, events, audit, and final evidence.

## Required environment

```env
WEBCHAT_VOICE_ENABLED=true
WEBCHAT_VOICE_PROVIDER=livekit
LIVEKIT_URL=wss://voice.your-domain.com
LIVEKIT_API_KEY=replace_me
LIVEKIT_API_SECRET=replace_me
WEBCHAT_VOICE_CONNECT_SRC=wss://voice.your-domain.com https://voice.your-domain.com
```

## Validation rules

- `WEBCHAT_VOICE_PROVIDER=mock` remains default.
- `WEBCHAT_VOICE_PROVIDER=livekit` requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
- Production `LIVEKIT_URL` must use `wss://`.
- `WEBCHAT_VOICE_CONNECT_SRC` must include the LiveKit `wss://` URL.
- Wildcard connect sources are forbidden.
- `LIVEKIT_API_SECRET` must never be logged or returned by API.
- Production recording remains disabled until a consent policy exists.

## Backend flow

When the visitor creates a voice session through the compatibility endpoint:

```text
POST /api/webchat/conversations/{conversation_id}/voice/sessions
```

NexusDesk validates the visitor token, selects the configured provider, creates or resolves a LiveKit room, stores the voice session with `provider=livekit`, issues a visitor token scoped to the returned room, and writes `voice.session.created` plus `voice.session.ringing` events.

When an agent accepts the call, NexusDesk validates ticket visibility, enforces first-agent-wins through `accepted_by_user_id`, issues an agent token scoped to the same room, and writes `voice.session.accepted` plus `voice.session.active` events.

When the call ends, NexusDesk updates the voice session status, attempts to close the LiveKit room, keeps the end flow successful even if provider close fails, writes a final event, and writes one `message_type=voice_call` system message into the WebChat thread.

## Response fields

The API remains backward compatible and still returns `room_name`. Provider-aware fields may also be returned:

```yaml
provider: livekit
provider_room_name: webcall_<voice_session_id>
participant_identity: visitor_<voice_session_id>_initial
participant_token: <short-lived room-scoped media token>
```

The participant token is for the specific visitor or authenticated agent joining the room. It must not be logged or exposed to unrelated users.

## Free self-hosted deployment path

```text
dev/local: LiveKit dev mode or docker compose
staging: public voice subdomain, TLS, WSS endpoint, websocket proxy, TURN/STUN check
production: independent voice domain, observability, firewall rules, scaling plan
```

External customer browsers must be able to reach a public HTTPS/WSS voice domain. Internal NexusDesk services may still use private networking, but WebCall media cannot rely only on private Tailscale addresses.

## Out of scope for Phase 1

- Visitor browser room page.
- Agent WebCall panel in the formal inbox.
- SIP trunk.
- PSTN phone numbers.
- Paid CPaaS provider integration.
- Recording.
- Realtime transcription.
- AI voice.
- Lifecycle webhook and timeout cleanup.

## Suggested checks

```bash
pytest backend/tests/test_livekit_voice_provider.py backend/tests/test_webchat_voice_api.py backend/tests/test_webchat_voice_static_headers.py backend/tests/test_webchat_voice_mock_ui_static.py
```
