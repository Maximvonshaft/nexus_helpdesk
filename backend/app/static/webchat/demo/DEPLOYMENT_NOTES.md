# Speedaf Showcase Integration Notes

This directory contains the public Speedaf showcase page served by NexusDesk at:

```text
/webchat/demo/
```

The legacy entry point `/webchat/demo.html` redirects to this page through a plain HTML meta refresh.

## Runtime behavior

The showcase chat sends customer messages to the NexusDesk WebChat Fast Lane endpoint:

```text
POST /api/webchat/fast-reply
```

The active request payload is intentionally minimal and must remain compatible with the backend `WebchatFastReplyRequest` schema:

```json
{
  "tenant_key": "speedaf_public_site",
  "channel_key": "speedaf_webchat",
  "session_id": "session_generated_in_browser",
  "client_message_id": "msg_generated_in_browser",
  "body": "customer message",
  "recent_context": []
}
```

Do not add unsupported visitor metadata fields or alternate context key names that are not accepted by the backend request model.

## Reply display rule

The browser may display a bot reply only when the backend response has:

```text
ok=true
reply=<non-empty string>
```

Any HTTP error, timeout, invalid JSON, negative backend result, empty reply, or backend error code must display only:

```text
Connection issue. Please try again.
```

The showcase page must not display local tracking answers, local bot answers, fake handoff confirmations, or locally invented parcel state.

## Voice entry

The showcase loads the optional customer-visible WebCall entry script:

```text
/webchat/voice-entry.js
```

Visibility remains controlled by the backend runtime config endpoint:

```text
GET /api/webchat/voice/runtime-config
```

When `WEBCHAT_VOICE_ENABLED=false`, the WebCall button stays hidden. When `WEBCHAT_VOICE_ENABLED=true`, the entry script displays the WebCall button and creates voice sessions through the existing WebChat voice runtime.
