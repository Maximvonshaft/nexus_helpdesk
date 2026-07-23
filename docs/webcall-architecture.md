# Canonical LiveKit Telephony Architecture

## Authority

LiveKit is the only real-time media plane for browser voice, AI voice, and SIP/PSTN. Nexus owns all business state; LiveKit owns media transport and Provider state.

```text
Browser or SIP participant
  → one LiveKit Room
  → one Nexus Conversation and Voice Session
  → governed Agent Runtime or canonical Handoff
  → OperatorAgentState capacity and scope routing
  → optional Ticket only for formal asynchronous responsibility
```

The sole human ownership authority is an accepted `WebchatHandoffRequest`, projected onto `WebchatConversation.active_agent_id`. A Voice Session may reference the accepted Handoff for lifecycle and evidence, but it does not own an independent accepted-agent field or second ownership state machine.

## Provider boundaries

- Signed LiveKit webhooks enter `TelephonyEventInbox` exactly once.
- `ChannelAccount(provider="voice")` remains the channel directory; `VoiceChannelConfiguration` is its one-to-one SIP/LiveKit extension.
- AI joins through explicit Agent Dispatch and sends business turns to the canonical Agent Runtime.
- SIP transfer and outbound participants use LiveKit SIP server APIs.
- Voice commands are idempotent `WebchatVoiceSessionAction` records with Provider/controller results.
- Warm transfer is an explicit same-Room consultation lifecycle: start, complete, or cancel. Target answer alone is not transfer completion.
- Recording remains disabled unless the configured notice/consent, retention, access, artifact, and erasure controls are executable and fail closed.

## Non-negotiable invariants

- One Conversation, Handoff, Voice Session, Room, Provider Event Inbox, and Voice Command authority.
- No browser PCM/AudioWorklet media edge or old audio WebSocket.
- No fallback Voice page or compatibility redirect; `/webcall/{voice_session_id}` is the only media page.
- No transfer-specific LLM or second Agent loop in the media worker.
- No second Voice queue, operator presence store, transcript store, or AI action model.
- Incoming offers expose no Provider credentials, room names, participant identities, or embedding-page metadata.
