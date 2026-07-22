# Canonical LiveKit Telephony Architecture

## Authority

LiveKit is the only real-time media plane for browser voice, AI voice and SIP/PSTN. Nexus owns business state; LiveKit owns media state.

```text
Browser or SIP participant
  -> one LiveKit room
  -> Nexus Conversation
  -> Generic Agent Runtime or canonical Handoff
  -> OperatorAgentState capacity and scope routing
  -> optional Ticket only for formal asynchronous responsibility
```

The sole customer ownership authority is the accepted `WebchatHandoffRequest`, projected onto `WebchatConversation.active_agent_id` and `WebchatVoiceSession.accepted_by_user_id`.

## Provider boundaries

- Signed LiveKit webhooks enter `TelephonyEventInbox` exactly once.
- `ChannelAccount(provider=voice)` remains the channel directory; `VoiceChannelConfiguration` is its one-to-one SIP/LiveKit extension.
- AI joins through explicit Agent Dispatch.
- SIP transfer and outbound participants use LiveKit SIP server APIs.
- Voice commands are idempotent `WebchatVoiceSessionAction` records with provider results.
- Recording is disabled until country-specific consent, retention, access and erasure policy is implemented.
