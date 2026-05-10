# WebChat AI-Only Fast Reply Runtime — OpenClaw Responses

## Decision

NexusDesk adds a new public customer-facing endpoint:

```text
POST /api/webchat/fast-reply
```

The browser only calls NexusDesk. NexusDesk privately calls OpenClaw Gateway `POST /v1/responses` as the AI runtime. The OpenClaw Gateway URL and bearer token must never be exposed to the browser, widget bundle, logs, or public internet.

## Why this exists

The legacy WebChat path is a chat/ticket runtime:

```text
/init -> Customer/Ticket/WebchatConversation
/messages -> WebchatMessage/TicketComment/TicketEvent/WebchatAITurn/BackgroundJob
polling -> customer sees reply
```

The new fast path is explicitly different:

```text
customer message -> /api/webchat/fast-reply -> OpenClaw /v1/responses -> AI reply returned directly
```

Normal successful replies do not create tickets, conversations, messages, ticket comments, ticket events, outbound messages, AI turns, OpenClaw transcripts, or background jobs.

## Public response contract

A successful normal response returns an AI-generated customer-visible reply:

```json
{
  "ok": true,
  "ai_generated": true,
  "reply_source": "openclaw_responses",
  "reply": "Hi, this is Speedy. How can I help you today?",
  "intent": "greeting",
  "tracking_number": null,
  "handoff_required": false,
  "handoff_reason": null,
  "ticket_creation_queued": false,
  "elapsed_ms": 123
}
```

If the AI asks for handoff, the AI reply is still returned immediately. NexusDesk may enqueue a DB-backed background job for the ticket snapshot:

```json
{
  "ok": true,
  "ai_generated": true,
  "reply_source": "openclaw_responses",
  "reply": "I’ll route this to a support specialist for checking.",
  "intent": "handoff",
  "tracking_number": "SF123456789",
  "handoff_required": true,
  "handoff_reason": "manual_review_required",
  "ticket_creation_queued": true,
  "elapsed_ms": 321
}
```

`recommended_agent_action` is intentionally not returned to the browser. It is only stored inside the handoff snapshot job payload.

If OpenClaw is unavailable or returns invalid/non-JSON output, NexusDesk does not generate a hardcoded customer-service reply:

```json
{
  "ok": false,
  "ai_generated": false,
  "reply": null,
  "error_code": "ai_unavailable",
  "retry_after_ms": 1500
}
```

The widget may show a non-reply UI state such as `Speedy is reconnecting...`, but must not display a template response as if it were an AI reply.

## OpenClaw contract

NexusDesk calls OpenClaw Gateway privately:

```text
POST ${OPENCLAW_RESPONSES_URL}
Authorization: Bearer <server-side secret>
x-openclaw-session-key: webchat-fast:<tenant>:<session>
```

The request selects the agent through the model field:

```json
{
  "model": "openclaw:webchat-fast",
  "stream": false,
  "max_output_tokens": 350,
  "instructions": "Return strict JSON only...",
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "..."}]
    }
  ]
}
```

The `webchat-fast` OpenClaw agent must have no tools in phase 1. Tool/function-call output is rejected by NexusDesk.

## Environment

Recommended server-side environment:

```env
WEBCHAT_FAST_AI_ENABLED=true
WEBCHAT_FAST_AI_PROVIDER=openclaw_responses
WEBCHAT_FAST_AI_TIMEOUT_MS=3000
WEBCHAT_FAST_AI_MAX_TIMEOUT_MS=5000
WEBCHAT_FAST_AI_HISTORY_TURNS=5
WEBCHAT_FAST_AI_MAX_PROMPT_CHARS=2500
WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS=60
WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS=30
WEBCHAT_FAST_HARD_FAIL_ON_NON_AI_REPLY=true
OPENCLAW_RESPONSES_URL=http://openclaw-gateway-private:18789/v1/responses
OPENCLAW_RESPONSES_AGENT_ID=webchat-fast
OPENCLAW_RESPONSES_TOKEN_FILE=/run/secrets/openclaw_gateway_token
OPENCLAW_RESPONSES_CONNECT_TIMEOUT_MS=500
OPENCLAW_RESPONSES_READ_TIMEOUT_MS=3000
OPENCLAW_RESPONSES_TOTAL_TIMEOUT_MS=3500
OPENCLAW_RESPONSES_POOL_MAX_CONNECTIONS=10
OPENCLAW_RESPONSES_POOL_MAX_KEEPALIVE=5
```

In production, `OPENCLAW_RESPONSES_TOKEN` is forbidden. Use `OPENCLAW_RESPONSES_TOKEN_FILE`.

## Security gates

Before enabling this in staging or production:

1. OpenClaw Gateway `/v1/responses` must not be publicly reachable.
2. Browser bundles and WebChat static files must not contain OpenClaw URL or token values.
3. The `webchat-fast` agent must have no tools in phase 1.
4. Output must be strict pure JSON. Markdown, surrounding prose, and function/tool calls are rejected.
5. Logs must not contain raw customer body, raw prompt, raw AI reply, OpenClaw token, or OpenClaw URL with secrets.

## Handoff snapshot

Only `handoff_required=true` can enqueue a DB-backed `webchat.handoff_snapshot` job. The worker creates a Ticket and TicketEvent containing the handoff snapshot. It does not create a full WebChat message timeline.

## Legacy compatibility

Existing WebChat APIs remain for historical conversations, old admin thread views, and rollback safety:

```text
POST /api/webchat/init
POST /api/webchat/conversations/{conversation_id}/messages
GET  /api/webchat/conversations/{conversation_id}/messages
POST /api/webchat/conversations/{conversation_id}/actions
GET  /api/webchat/admin/conversations
GET  /api/webchat/admin/tickets/{ticket_id}/thread
POST /api/webchat/admin/tickets/{ticket_id}/reply
```

New customer traffic should use `/api/webchat/fast-reply` once the widget is switched in the next phase.
