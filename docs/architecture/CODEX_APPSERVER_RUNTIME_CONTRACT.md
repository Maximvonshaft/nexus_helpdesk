# Codex App-server Runtime Contract Alignment

This document records the NexusDesk runtime contract for the Codex app-server path.

## Purpose

NexusDesk keeps the production customer-service runtime, but aligns the low-level Codex app-server protocol with OpenClaw's proven native contract where it matters:

```text
chatgptAuthTokens -> account/login/start -> thread/start -> turn/start
```

## Login contract

Nexus sends the app-server login payload with the native Codex/OpenClaw shape:

```json
{
  "type": "chatgptAuthTokens",
  "accessToken": "...",
  "chatgptAccountId": "...",
  "chatgptPlanType": "plus"
}
```

The backend must never expose `accessToken` outside the private runtime boundary.

`chatgptAccountId` resolution follows the OpenClaw fallback principle:

```text
account_id -> email -> profile_id -> credential id
```

This prevents valid OAuth credentials without an explicit account id from failing the runtime's required login contract.

## Thread start contract

Nexus uses short-lived ephemeral threads for customer-service fast reply. It does not use OpenClaw's long-lived thread binding yet. However, the thread start payload now mirrors the important app-server fields:

```json
{
  "model": "gpt-5.3-codex-spark",
  "cwd": "/tmp/nexus-codex-runtime/webchat-workdir",
  "approvalPolicy": "never",
  "approvalsReviewer": "user",
  "sandbox": "read-only",
  "serviceName": "NexusDesk",
  "config": {
    "features.code_mode": true,
    "features.code_mode_only": false,
    "project_doc_max_bytes": 0
  },
  "developerInstructions": "...",
  "dynamicTools": [],
  "experimentalRawEvents": false,
  "persistExtendedHistory": false,
  "serviceTier": "priority"
}
```

## Turn start contract

Nexus starts a single turn in the ephemeral thread with:

```json
{
  "threadId": "...",
  "input": [{ "type": "text", "text": "...", "text_elements": [] }],
  "cwd": "/tmp/nexus-codex-runtime/webchat-workdir",
  "approvalPolicy": "never",
  "approvalsReviewer": "user",
  "sandboxPolicy": { "type": "readOnly", "networkAccess": false },
  "dynamicTools": [],
  "model": "gpt-5.3-codex-spark",
  "serviceTier": "priority",
  "effort": "low",
  "collaborationMode": {
    "mode": "default",
    "settings": {
      "model": "gpt-5.3-codex-spark",
      "reasoning_effort": "low",
      "developer_instructions": null
    }
  }
}
```

## Intentional Nexus differences from OpenClaw

Nexus intentionally keeps these differences:

1. `threadMode=ephemeral`: each fast reply request creates a fresh thread and unsubscribes after completion.
2. `dynamicTools=[]`: customer-service fast reply is reply-only; tool execution remains controlled by Nexus.
3. `persistExtendedHistory=false`: customer-service context comes from Nexus ticket/webchat state, not a long native Codex transcript.
4. `sandbox=read-only`: no filesystem mutation is allowed by the reply runtime.
5. `approvalPolicy=never`: the reply runtime must not block a customer interaction waiting for human approval inside Codex.

## Performance implication

OpenClaw remains faster for long-running agent sessions because it can reuse and resume native Codex threads. Nexus pays a thread-start cost for each reply, but gains better customer-service isolation, stricter output validation, per-request timeout, queue backpressure, audit logs, and fallback control.

## Verification

Unit tests assert that `tools/nexus-codex-runtime/src/thread-runner.ts` emits OpenClaw-aligned `thread/start` and `turn/start` fields.

Backend provider-runtime tests assert that the bridge login payload resolves `chatgptAccountId` using account id, email, or profile id rather than sending null.
