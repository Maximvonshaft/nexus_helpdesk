# WebChat Security Model

## Public surface

The public WebChat surface is:

- `POST /api/webchat/init`
- `GET /api/webchat/conversations/{conversation_id}/messages`
- `POST /api/webchat/conversations/{conversation_id}/messages`
- `POST /api/webchat/conversations/{conversation_id}/actions`
- `/webchat/widget.js`
- `/webchat/demo.html`

Admin APIs remain authenticated.

## Origin policy

`WEBCHAT_ALLOWED_ORIGINS` is the production allowlist. Missing origin is accepted only when `WEBCHAT_ALLOW_NO_ORIGIN` or local/test/development settings allow it. Production deployments should not accept arbitrary origins.

## Visitor token policy

Visitor reads and actions require `conversation_id + visitor_token`. Header transport is preferred through `X-Webchat-Visitor-Token`. Legacy query/body token transport is opt-in only through `WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT`.

## XSS policy

- Visitor text is stored as text and rendered through React escaping or DOM `textContent`.
- Card payload rejects executable markup and unsafe URL schemes.
- Widget renderers do not use `innerHTML` for card content.
- Admin UI uses `sanitizeDisplayText` and does not use `dangerouslySetInnerHTML`.

## AI and fact safety

AI is not allowed to directly generate frontend-rendered HTML/JS/CSS/iframe or arbitrary card JSON. AI/rules produce intent; backend-owned card factory generates allowlisted payloads.

Without real tool/database evidence, WebChat must not claim:

- parcel status
- delivery time
- successful address change
- successful reschedule
- compensation/refund approval
- customs result
- driver contact

High-risk intents fall back to safe text or handoff.

## Outbound safety

WebChat local ACK, AI fallback, card delivery, and handoff ACK are local-only semantics. They are not external provider sends and must not be counted as WhatsApp, Telegram, SMS, or Email dispatch.

External outbound channels are only:

- `whatsapp`
- `telegram`
- `sms`
- `email`

## Upload policy

Photo upload is schema-reserved but not enabled in this release. Any future upload implementation must enforce:

- MIME allowlist
- extension allowlist
- max byte size
- object-storage abstraction
- no public local filesystem paths
- malware/content scanning where available

## iframe / CSP policy

Do not globally relax `X-Frame-Options` or `frame-ancestors`. If iframe embed becomes necessary, implement a dedicated `/webchat/embed` route with isolated headers and narrow origin policy.

## Logging policy

Structured logs must not include raw visitor tokens, secrets, prompts, bridge credentials, OpenClaw internal paths, stack traces, or full PII bodies. Use conversation/ticket/message IDs and safe reason codes.
