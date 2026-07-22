# WebCall Current State

## Product boundary

Nexus exposes two customer communication capabilities over the canonical Conversation runtime:

1. **Human WebCall** — browser voice between a visitor and an operator.
2. **Live AI Voice** — realtime voice handled by the governed Agent Runtime.

They are separate capabilities with separate feature flags, API authorities and runtime services. Neither capability is a second inbox, ticket system, handoff queue or conversation model.

## Canonical authorities

### Human WebCall

- API: `backend/app/api/webchat_voice.py`
- lifecycle service: `backend/app/services/webchat_voice_service.py`
- provider interface: `backend/app/services/voice_provider.py`
- LiveKit provider: `backend/app/services/livekit_voice_provider.py`
- feature flag: `WEBCHAT_HUMAN_CALL_ENABLED`

The LiveKit provider can create and close rooms, issue participant tokens and query room state. The mock provider remains a bounded test implementation and is not a production transport authority.

### Live AI Voice

- API: `backend/app/api/webchat_live_voice.py`
- orchestration: `backend/app/services/live_voice_orchestration_service.py`
- feature flag: `WEBCHAT_LIVE_AI_VOICE_ENABLED`

The AI voice capability uses the canonical Conversation and Agent Runtime authorities. It does not create a parallel AI model, provider router, handoff lifecycle or ticket workflow.

## Conversation-first invariant

A voice session belongs to a Conversation. A Ticket is optional context and is created only by a governed business action when durable follow-up is required. Starting, accepting, rejecting, annotating or ending a voice session must not create a Ticket merely to satisfy a technical service signature.

## Production posture

Repository support for LiveKit is not evidence that production voice traffic is enabled. Controlled deployment keeps Human WebCall and Live AI Voice disabled unless an explicitly authorized deployment supplies complete credentials, origins, network routing, monitoring and operational readiness evidence.

The aggregate `WEBCHAT_VOICE_ENABLED` flag is compatibility-only. Production activation must use the two explicit capability flags.

## Not yet implied by repository presence

The source tree does not by itself prove that the following are operational in a production environment:

- public DNS and TLS for the LiveKit endpoint;
- TURN reachability from intended customer networks;
- approved recording and retention policy;
- realtime transcription quality and language coverage;
- SIP or PSTN ingress;
- production call-volume capacity;
- operator staffing and incident readiness.

These require deployment evidence bound to an immutable release identity.

## Verification

Use the canonical repository verification and voice-specific tests and probes. Do not treat this document, a green unit test or the existence of a provider class as production enablement authority.
