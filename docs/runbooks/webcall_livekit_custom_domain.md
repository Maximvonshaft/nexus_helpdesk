# LiveKit Custom Domain for Canonical WebCall

## Purpose

This runbook describes the supported pattern for exposing a LiveKit deployment through an approved custom media domain while keeping Nexus as the business-state authority.

```text
Browser or SIP participant
→ approved LiveKit custom domain
→ LiveKit deployment
→ one canonical Room
→ Nexus Conversation / Handoff / Voice Session
```

The custom domain is a network boundary only. It must not create a second WebCall application, proxy business APIs, or introduce another call-control authority.

## Required configuration

Use deployment secrets for credentials and keep the media page restricted to `/webcall/{voice_session_id}`.

```env
WEBCHAT_HUMAN_CALL_ENABLED=true
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=livekit
WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES=/webcall
LIVEKIT_URL=wss://voice.example.com
WEBCHAT_VOICE_CONNECT_SRC=wss://voice.example.com https://voice.example.com
LIVEKIT_API_KEY_FILE=/run/secrets/livekit_api_key
LIVEKIT_API_SECRET_FILE=/run/secrets/livekit_api_secret
LIVEKIT_AGENT_SHARED_SECRET_FILE=/run/secrets/livekit_agent_shared_secret
LIVEKIT_WEBHOOK_ENABLED=true
```

The public runtime-config endpoint may expose only bounded capability facts and the public LiveKit URL. API keys, API secrets, participant tokens, visitor tokens, controller credentials, room names, participant identities, and Provider topology remain server-side.

## Reverse-proxy requirements

The custom domain must support the complete LiveKit browser and server API surface required by the selected deployment, including WebSocket upgrade, TLS/SNI, request timeouts, and any media connectivity requirements documented by LiveKit.

Required controls:

- valid public TLS certificate;
- upstream Host and SNI set to the actual LiveKit deployment;
- WebSocket upgrade headers preserved;
- buffering and caching disabled for realtime paths;
- no credential logging;
- no open proxy behavior;
- health and certificate monitoring.

A proxy that makes only one browser path reachable but breaks Room, SIP, Egress, Agent Dispatch, or server API calls is not production-ready.

## Verification

Before activation:

1. The exact candidate Head passes Canonical Acceptance.
2. The custom domain resolves to the intended proxy and certificate.
3. Browser WebSocket connection uses the custom domain.
4. Backend Room, participant, command, webhook, and Agent Dispatch operations succeed through the supported API endpoint.
5. `/webcall/{voice_session_id}` receives microphone permission only after explicit join or accept.
6. The incoming-offer response contains no LiveKit credentials or topology.
7. Hold, resume, DTMF, cold transfer, and warm consultation start/complete/cancel obtain Provider acknowledgement.
8. A Provider or proxy failure remains visibly unconfirmed and does not fabricate success.

## Rollback

Disable the explicit capabilities and leave the Provider fail-closed:

```env
WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=mock
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false
```

In production, `mock` must not simulate a successful call. Recreate the controlled services after changing deployment inputs and verify `/readyz` before restoring traffic.

Changing `LIVEKIT_URL` back to the approved direct deployment URL is permitted only when CSP/connect-src, certificates, and Provider validation are updated together and the controlled call matrix is repeated.
