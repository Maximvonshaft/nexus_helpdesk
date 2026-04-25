# OpenClaw Mock Testing

## Mock server

`backend/scripts/mock_openclaw_server.py` provides deterministic mock endpoints for Round A smoke tests.

```bash
python3 backend/scripts/mock_openclaw_server.py --port 18792
```

Health:

```bash
curl http://127.0.0.1:18792/healthz
```

## Supported mock tools

- `GET /conversation_get`
- `GET /messages_read`
- `GET /attachments_fetch`
- `GET /events_poll`
- `GET /events_wait`
- `POST /messages_send`
- `GET /sent_messages`

## Fixtures

- `backend/tests/fixtures/openclaw/conversation.json`
- `backend/tests/fixtures/openclaw/messages.json`
- `backend/tests/fixtures/openclaw/events.json`
- `backend/tests/fixtures/openclaw/attachments.json`

## Scenarios covered

- Complete route with `channel`, `recipient`, `accountId`, `threadId`.
- Incomplete route.
- Missing `sessionKey`.
- Duplicate transcript `message_id`.
- Non-user transcript role.
- Attachment-only message.
- Metadata-only attachment.
- Base64 attachment.
- Text attachment.
- Private URL attachment that must not be fetched in live code.
- `messages_send` success.
- `messages_send` missing route failure.
- Forced send failure via `forceFailure`.

## Differences from real OpenClaw

The mock server does not implement the real OpenClaw MCP stdio protocol or Gateway WebSocket lifecycle. It is a deterministic contract test harness for route, transcript, event, attachment, and send semantics. Real OpenClaw live validation is still required before customer-channel production enablement.
