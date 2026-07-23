# Canonical WebCall P0 Closure

## Scope

The production Voice capability is one LiveKit telephony product integrated with the existing Nexus Conversation, Handoff, Agent Runtime, OperatorAgentState, ChannelAccount, durable Voice Command, Provider Event Inbox, Workspace, Channels, and Control Tower authorities.

```text
WebChat or SIP caller
→ one LiveKit Room
→ one Conversation and Voice Session
→ governed AI or canonical Handoff
→ Provider-confirmed call controls
→ required after-call outcome
```

No fallback media page, aggregate Voice switch, second queue, second AI loop, or compatibility transport is supported.

## Fail-closed deployment stance

Deploy with both explicit capabilities disabled until the actual Provider prerequisites and controlled call matrix are complete:

```env
WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=mock
WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES=/webcall
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false
```

A production process must reject the mock Provider rather than simulate success.

## Required closure evidence

The exact PR Head must pass the single Canonical Acceptance workflow, including:

- static service-authority and telephony-residue gates;
- complete backend regression;
- PostgreSQL migration/acceptance;
- frontend lint, types, tests, build, and browser journeys;
- image build, migration, startup, readiness, Trivy, and SBOM;
- secret scan, SAST, dependency audit, and CodeQL;
- final required gate.

Runtime verification with real credentials must prove:

- one Room, Conversation, Voice Session, Handoff, and owner;
- incoming capability/scope filtering and one-call capacity;
- AI suspension before post-handoff Tool or customer-visible side effects;
- hold, resume, DTMF, cold transfer, and warm consultation start/complete/cancel;
- customer disconnect and reconnect grace behavior;
- recording/consent/artifact governance when enabled;
- required wrap-up and deterministic capacity release;
- tenant-isolated operations metrics with no sensitive labels.

## Rollback

Disable the explicit capability flags and preserve durable evidence, Conversations, Handoffs, Voice Sessions, Commands, and Provider Inbox rows. Do not drop telephony tables or create a compatibility path during an emergency rollback.
