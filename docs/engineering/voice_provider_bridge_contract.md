# WebCall AI Voice Provider Bridge Contract

This PR does not bind the product to one vendor SDK. Production can use any approved bridge that implements these HTTP contracts and reads secrets from files only.

## STT

`POST STT_ENDPOINT`

- Auth: `Authorization: Bearer <contents of STT_API_KEY_FILE>`
- Multipart field: `audio`
- Current worker sends `audio/wav`. LiveKit raw PCM is wrapped with a WAV header in memory; raw audio is not written to disk.
- Form fields: `language`, `sample_rate`, `channels`

Response:

```json
{
  "text": "Where is package SF123456789CN?",
  "language": "en",
  "confidence": 91
}
```

## LLM

`POST LLM_ENDPOINT`

- Auth: `Authorization: Bearer <contents of LLM_API_KEY_FILE>`
- JSON body includes `system`, `input`, `language`, and `response_format=json`.
- The bridge must enforce the supplied system prompt: logistics support only, read-only tracking, no invented status, and human handoff for unsafe or unavailable requests.

Response:

```json
{
  "response_text": "I cannot verify that safely right now. I will hand this to a human agent.",
  "intent": "tracking_lookup_not_configured",
  "handoff_required": true,
  "handoff_reason": "tracking_lookup_not_configured"
}
```

## TTS

`POST TTS_ENDPOINT`

- Auth: `Authorization: Bearer <contents of TTS_API_KEY_FILE>`
- JSON body: `text`, `language`, `voice`, `format=wav`
- Response body: WAV audio or PCM16.
- Response `Content-Type`: `audio/wav`, `audio/pcm`, `audio/l16`, or `application/octet-stream`.

## Tracking

`TRACKING_LOOKUP_ENDPOINT` and `TRACKING_LOOKUP_API_KEY_FILE` are reserved for an approved read-only Speedaf bridge. Until that bridge is configured, the tool returns `not_configured` and the AI hands off. Cancel, address update, and work-order actions remain blocked.

## Secret Handling

Inline provider API keys are rejected in production. Mount secrets under `/run/secrets` or an equivalent root-owned path and point `*_API_KEY_FILE` or `LIVEKIT_API_SECRET_FILE` at those files.
