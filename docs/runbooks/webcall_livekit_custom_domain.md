# WebCall LiveKit Custom Domain Runbook

## Purpose

This runbook documents the production pattern for using `voice.leakle.com` as the public WebCall media domain.

Validated production pattern:

```text
NexusDesk LIVEKIT_URL=wss://voice.leakle.com
voice.leakle.com -> Nginx reverse proxy -> speedaf-th0pg5cj.livekit.cloud
WebCall create -> agent accept -> end -> exactly-one voice_call evidence message
```

## Scope

In scope:

- LiveKit custom domain reverse proxy.
- NexusDesk WebCall runtime checks.
- Post-deploy synthetic E2E verification.
- Evidence closure verification.

Out of scope:

- Recording.
- Transcription.
- AI voice bot.
- SIP/PSTN.
- Paid telephony provider integration.

## Architecture

```text
Visitor browser
  -> https://www.leakle.com/webchat/demo/
  -> NexusDesk WebCall API
  -> NexusDesk backend
  -> https://voice.leakle.com
  -> Nginx proxy
  -> https://speedaf-th0pg5cj.livekit.cloud

Visitor / agent WebRTC media
  -> wss://voice.leakle.com/rtc
  -> Nginx websocket proxy
  -> wss://speedaf-th0pg5cj.livekit.cloud/rtc
```

The custom domain must proxy both browser media traffic and backend room-management API calls. A domain that only makes `/rtc` reachable is not sufficient.

## Runtime configuration

Server-only runtime values should be set in deployment environment files, not committed to Git.

Required non-secret values:

```env
WEBCHAT_HUMAN_CALL_ENABLED=true
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=livekit
LIVEKIT_URL=wss://voice.leakle.com
WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES=/webchat,/webchat/voice,/webcall,/webchat-voice
WEBCHAT_VOICE_CONNECT_SRC=wss://voice.leakle.com https://voice.leakle.com
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false
WEBCHAT_VOICE_SESSION_TTL_SECONDS=900
WEBCHAT_VOICE_MAX_ACTIVE_PER_CONVERSATION=1
WEBCHAT_VOICE_RATE_LIMIT_WINDOW_SECONDS=60
WEBCHAT_VOICE_RATE_LIMIT_MAX_REQUESTS=5
```

LiveKit credentials must remain on the server or in a mounted deployment secret file. Do not commit them.

## Nginx template

Use:

```text
deploy/nginx/livekit_voice_custom_domain.conf.template
```

Required substitutions:

```text
__VOICE_HOST__=voice.leakle.com
__LIVEKIT_UPSTREAM_HOST__=speedaf-th0pg5cj.livekit.cloud
__SSL_CERTIFICATE__=/etc/letsencrypt/live/voice.leakle.com/fullchain.pem
__SSL_CERTIFICATE_KEY__=/etc/letsencrypt/live/voice.leakle.com/privkey.pem
```

Critical behavior:

- Preserve websocket upgrade headers.
- Use the upstream LiveKit Cloud hostname for upstream Host and SNI.
- Disable buffering and cache for the LiveKit proxy location.

## Deploy

```bash
nginx -t
systemctl reload nginx

docker compose --env-file deploy/.env.prod -f deploy/docker-compose.server.yml up -d --no-deps --force-recreate app
```

Verify:

```bash
curl -sS http://127.0.0.1:18081/readyz
curl -sS https://www.leakle.com/api/webchat/voice/runtime-config
```

Expected public runtime config:

```json
{
  "enabled": true,
  "provider": "livekit",
  "livekit_url": "wss://voice.leakle.com",
  "recording_enabled": false,
  "transcription_enabled": false
}
```

## Post-deploy probe

Run:

```bash
SUPPORT_BASE=https://www.leakle.com \
VOICE_HOST=voice.leakle.com \
EXPECTED_LIVEKIT_URL=wss://voice.leakle.com \
RUN_SYNTHETIC_E2E=1 \
bash scripts/probe_webcall_livekit_custom_domain.sh
```

Expected final verdict:

```text
WEBCALL_LIVEKIT_CUSTOM_DOMAIN_OK
```

## Manual browser E2E

1. Open `https://www.leakle.com/webchat/demo/`.
2. Start WebCall.
3. Visitor clicks `Join WebCall` and allows microphone.
4. Agent opens `/webchat` or `/webchat-voice`.
5. Agent selects latest ticket and clicks `Accept WebCall`.
6. Confirm two-way audio.
7. End the call.
8. Confirm the ticket timeline contains one `Voice call ended · Ns` item.

## Rollback

Disable WebCall:

```env
WEBCHAT_VOICE_ENABLED=false
WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=mock
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false
```

Fallback to direct LiveKit Cloud URL while keeping WebCall enabled:

```env
LIVEKIT_URL=wss://speedaf-th0pg5cj.livekit.cloud
WEBCHAT_VOICE_CONNECT_SRC=wss://speedaf-th0pg5cj.livekit.cloud https://speedaf-th0pg5cj.livekit.cloud
```

Recreate the app container after runtime env changes.

## Known limitations

Still separate hardening work:

- LiveKit webhook ingestion.
- Missed-call cleanup scheduling proof.
- Agent queue push via WebSocket/SSE.
- Recording/transcription/AI voice.
