# Nexus WebChat Widget

## What It Is

Nexus WebChat is the public customer-side support runtime for customer
websites. A website embeds one JavaScript snippet. Visitor messages are stored
in durable WebChat conversations, processed by the WebChat AI turn queue, and
answered only through the unified private AI Runtime.

Customer-visible fallback text, local template replies, and direct browser
access to internal Runtime or Speedaf credentials are not allowed.

## Basic Embed

```html
<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" async></script>
```

The showcase page at `/webchat/demo/` also uses this same script. It must not
ship a separate customer-chat implementation.

## Runtime Flow

```text
browser -> POST /api/webchat/init
browser -> POST /api/webchat/conversations/{conversation_id}/messages
worker  -> Speedaf facts / knowledge context when applicable
worker  -> unified private AI Runtime
browser <- WebSocket message.created or GET /api/webchat/conversations/{conversation_id}/messages
```

The visitor token is sent through `X-Webchat-Visitor-Token`. It must not be
placed in URLs.

## Optional Attributes

- `data-tenant`: tenant key, default `default`.
- `data-channel`: channel key, default `website`.
- `data-title`: widget header title.
- `data-subtitle`: widget header subtitle.
- `data-assistant-name`: public assistant name.
- `data-locale`: locale hint.
- `data-button-label`: floating launcher label.
- `data-accent-color`: optional brand accent color.
- `data-security-note`: short safety note below the composer.
- `data-auto-open`: set to `true` for controlled demos that should open the
  widget on page load.
- `data-input-placeholder`: input placeholder.
- `data-send-label`: send button label.
- `data-live-voice-mode`: set to `edge-card` to show the inline VOIP entry.
- `data-live-voice-ws-path`: same-origin WebSocket path for live voice.
- `data-live-voice-label`: voice button label.
- `data-api-base`: API origin override for controlled staging/demo use.
- `data-websocket`: set to `false` only for a controlled polling fallback test.
- `data-poll-ms`: fallback polling interval when idle.
- `data-pending-poll-ms`: fallback polling interval while an AI reply is pending.

## Page Triggers

Links or buttons can open the widget without custom page JavaScript:

```html
<button type="button" data-open-chat>Open support</button>
```

Forms can hand a value, such as a tracking number, to the same widget:

```html
<form data-webchat-form data-webchat-input="#trackingInput">
  <input id="trackingInput" />
  <button type="submit">Track</button>
</form>
```

## Production Rules

- Customer-visible text comes from the unified AI Runtime.
- Knowledge is supplied as Runtime context, not as a browser or backend reply
  template.
- If AI is unavailable, the backend returns no customer-visible fallback reply.
- WebSocket is the preferred delivery path; `after_id` polling is the safe
  fallback.
