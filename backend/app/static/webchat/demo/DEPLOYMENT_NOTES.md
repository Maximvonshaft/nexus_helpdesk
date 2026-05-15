# Speedaf Showcase Integration Notes

This directory contains the public Speedaf showcase page served by NexusDesk at:

```text
/webchat/demo/
```

The legacy entry point `/webchat/demo.html` redirects to this page through a plain HTML meta refresh.

## Runtime behavior

The showcase chat is not a mock chat. Customer messages are sent to the NexusDesk WebChat Fast Lane endpoint:

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

Do not add unsupported fields such as `visitor.source` or `recent_context[].content`.

## Reply display rule

The browser may display a bot reply only when the backend response has:

```text
ok=true
reply=<non-empty string>
```

Any HTTP error, timeout, invalid JSON, `ok=false`, empty reply, or backend `error_code` must display only:

```text
Connection issue. Please try again.
```

The showcase page must not display local fake tracking status, fallback bot replies, fake voice support, fake handoff confirmations, or locally invented parcel state.

## Voice support

This showcase does not expose a customer-visible voice entry. If voice support is later required, integrate the existing NexusDesk `/webchat/voice-entry.js` runtime and let `/api/webchat/voice/runtime-config` control visibility.
