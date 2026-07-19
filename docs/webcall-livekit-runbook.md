# WebCall LiveKit Provider Runbook

## Scope

This runbook covers WebCall Inbound through the current staging-proof closure:

```text
visitor browser -> NexusDesk voice session -> LiveKit room -> agent browser -> ticket evidence
```

LiveKit provides media transport only. NexusDesk remains the system of record for conversation, ticket, agent ownership, state transitions, events, audit, and final `message_type=voice_call` evidence.

This runbook does **not** authorize AI voice, recording, realtime transcription, SIP, PSTN, phone numbers, paid CPaaS, or outbound calling.

## Required environment

```env
WEBCHAT_HUMAN_CALL_ENABLED=true
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=livekit
LIVEKIT_URL=wss://voice.your-domain.com
LIVEKIT_API_KEY=replace_me
LIVEKIT_API_SECRET=replace_me
WEBCHAT_VOICE_CONNECT_SRC=wss://voice.your-domain.com https://voice.your-domain.com
WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES=/webchat/voice,/webcall
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false
```

## Validation rules

- `WEBCHAT_VOICE_PROVIDER=mock` remains the rollback-safe default.
- `WEBCHAT_VOICE_PROVIDER=livekit` requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
- Production `LIVEKIT_URL` must use `wss://`.
- `WEBCHAT_VOICE_CONNECT_SRC` must include the LiveKit `wss://` URL.
- Wildcard connect sources are forbidden.
- `LIVEKIT_API_SECRET` and `LIVEKIT_API_KEY` must never be logged, returned by API, or shipped to the browser bundle.
- Production recording remains disabled until a consent policy is implemented.

## Domain model

Use separate domains for business runtime and media runtime:

```text
support.<domain> -> NexusDesk app, WebChat, WebCall pages
voice.<domain>   -> LiveKit HTTPS/WSS media service
```

External customer browsers must be able to reach the public `wss://voice.<domain>` endpoint. WebCall media must not rely on a private Tailscale-only address.

## Backend flow

When the visitor creates a voice session through the compatibility endpoint:

```text
POST /api/webchat/conversations/{conversation_id}/voice/sessions
```

NexusDesk validates the visitor token, selects the configured provider, creates or resolves a LiveKit room, stores the voice session with `provider=livekit`, issues a visitor token scoped to the room, and writes `voice.session.created` plus `voice.session.ringing` events.

If the provider room is created but later persistence/token/participant work fails, NexusDesk must attempt to close the provider room as compensation. Compensation failure is warning-only and must not hide the original exception.

When an agent accepts the call, NexusDesk validates ticket visibility, enforces first-agent-wins through `accepted_by_user_id`, issues an agent token scoped to the same room, and writes `voice.session.accepted` plus `voice.session.active` events.

When the call ends, NexusDesk updates the voice session status, attempts to close the LiveKit room, keeps the end flow successful even if provider close fails, writes a final event, and writes one `message_type=voice_call` system message into the WebChat thread.

## Browser flow

### Visitor

1. The public `voice-entry.js` checks `GET /api/webchat/voice/runtime-config`.
2. If enabled, it shows the WebCall entry button.
3. On click, it creates/binds a WebChat conversation and creates a voice session.
4. It opens `/webcall/{voice_session_id}` with short-lived join context in the URL fragment.
5. The `/webcall` page clears the URL fragment on load.
6. Microphone permission is requested only after the visitor clicks `Join WebCall`.
7. The visitor joins the LiveKit room using the backend-issued participant token.

### Agent

1. The agent opens `/webchat-voice` in the NexusDesk admin app.
2. The agent selects the WebChat ticket.
3. The Agent WebCall panel lists voice sessions for the selected ticket.
4. The agent clicks `Accept WebCall`.
5. The frontend calls the admin accept endpoint and receives an agent participant token.
6. Microphone permission is requested only after `Accept WebCall` is clicked.
7. The agent joins the same LiveKit room, publishes local audio, and subscribes to visitor audio.
8. `Mute`, `Unmute`, and `End WebCall` are available from the panel.

## Staging proof checklist

### DNS and TLS

- `support.<domain>` resolves publicly.
- `voice.<domain>` resolves publicly.
- Both domains have trusted TLS certificates.
- `https://support.<domain>/healthz` returns 200.
- `https://support.<domain>/readyz` returns 200.
- `wss://voice.<domain>` is browser reachable from a normal external network.

### NexusDesk runtime config

Run:

```bash
curl -sS https://support.<domain>/api/webchat/voice/runtime-config
```

Expected:

```json
{
  "enabled": true,
  "provider": "livekit",
  "livekit_url": "wss://voice.<domain>",
  "recording_enabled": false,
  "transcription_enabled": false
}
```

The response must not contain API key, API secret, participant token, visitor token, or password values.

### Header gates

Run:

```bash
curl -I https://support.<domain>/
curl -I https://support.<domain>/webcall/probe-route
```

Expected:

- Root/default pages keep `microphone=()`.
- `/webcall` pages use `microphone=(self)`.
- `/webcall` Content-Security-Policy `connect-src` includes `wss://voice.<domain>`.

### Automated probe

Run:

```bash
PUBLIC_BASE_URL=https://support.<domain> \
VOICE_WSS_URL=wss://voice.<domain> \
bash scripts/probe_webcall_runtime.sh
```

The script writes artifacts to:

```text
/tmp/nexus_webcall_probe_<timestamp>/
```

Review `FINAL_WEB_CALL_PROBE_REPORT.md` before manual testing.

### Manual browser proof

1. Open the public WebCall entry as visitor.
2. Confirm no microphone prompt appears before visitor clicks `Join WebCall`.
3. Click `Join WebCall` and allow microphone.
4. Open NexusDesk `/webchat-voice` as an authenticated agent.
5. Select the matching WebChat ticket.
6. Confirm `Incoming WebCall` appears.
7. Click `Accept WebCall` and allow microphone.
8. Confirm visitor and agent are in the same LiveKit room.
9. Confirm two-way audio works.
10. Test mute/unmute on the agent side.
11. Click `End WebCall`.
12. Confirm the ticket timeline receives exactly one `message_type=voice_call` evidence message.

## Required pre-merge checks

```bash
PYTHONPATH=backend pytest \
  backend/tests/test_webchat_voice_api.py \
  backend/tests/test_livekit_voice_provider.py \
  backend/tests/test_webchat_voice_static_headers.py \
  backend/tests/test_webchat_voice_mock_ui_static.py \
  backend/tests/test_webchat_voice_room_compensation.py \
  -q

npm --prefix webapp run typecheck
npm --prefix webapp run build
npm --prefix webapp test
```

## Out of scope

- SIP trunk.
- PSTN phone numbers.
- Paid CPaaS provider integration.
- Recording.
- Realtime transcription.
- AI voice.
- Voice bot.
- Outbound call.
- Audio relay through FastAPI.
- Lifecycle webhook and missed-call cleanup worker. These belong to the next phase.

## Rollback

Feature-flag rollback:

```env
WEBCHAT_VOICE_ENABLED=false
WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=mock
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false
```

Application rollback:

```bash
git checkout main
# or redeploy the previous known-good image tag
```

Keep dormant voice tables in place during emergency rollback. Do not drop voice tables without a separate approved cleanup migration.
