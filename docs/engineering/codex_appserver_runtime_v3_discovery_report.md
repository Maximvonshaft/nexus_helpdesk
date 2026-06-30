# Codex App-Server Runtime v3 Discovery Report

Generated UTC: 2026-05-25T09:49:42Z
Status: **BLOCKED_UNKNOWN**

## Verdict

Discovery is blocked for production approval: dummy token did not produce an assistant reply, but no natural terminal turn state was observed. Engineering candidate work may proceed, but do not increase canary or approve production until terminal auth behavior is known.

Failure reasons:
- `BLOCKED_UNKNOWN_dummy_terminal_turn_no_terminal_state`

## Executable

- Command path: `C:\Users\Maxim\AppData\Roaming\npm\codex.cmd`
- Version exit code: `0`
- Version stdout: `codex-cli 0.125.0`
- Start command shape: `['C:\\Users\\Maxim\\AppData\\Roaming\\npm\\codex.cmd', 'app-server', '--listen', 'stdio://']`
- Isolated CODEX_HOME: `C:\Users\Maxim\Documents\nexus\probe_reports\codex_appserver_runtime_v3_discovery\isolated_codex_home`
- Isolated HOME: `C:\Users\Maxim\Documents\nexus\probe_reports\codex_appserver_runtime_v3_discovery\isolated_home`
- Cleared auth env vars: `['CODEX_API_KEY', 'OPENAI_API_KEY', 'OPENAI_ACCESS_TOKEN', 'CODEX_ACCESS_TOKEN', 'EXTERNAL_CHANNEL_HOME']`

## ExternalChannel Reference Notes

- Reference repo: `https://github.com/external_channel/external_channel`
- Current main inspected: `4a45098a866949f8cbb790840fd7ee1533855450`
- Reference pack pinned commit: `732cf542404f06c5e978ec37936a179d8c339d5e`
- `package.json` does not expose `extensions/codex/src/app-server/*` as stable public package exports.
- `shared-client.ts` initializes one reusable app-server client and then applies auth through `account/login/start`.
- `auth-bridge.ts` isolates Codex home for stdio startup; this probe sets an isolated `CODEX_HOME` for the same boundary.
- Phase 1D harness registers a notification handler before `thread/start` / `turn/start`, buffers early notifications, correlates `params.threadId` / `params.turnId` and `params.turn.threadId` / `params.turn.id`, and treats `turn/completed` or non-retry `error` as terminal.

## JSON-RPC Initialize

- OK: `True`
- Error: `None`
- Response: `{"id": 1, "result": {"codexHome": "\\\\?\\C:\\Users\\Maxim\\Documents\\nexus\\probe_reports\\codex_appserver_runtime_v3_discovery\\isolated_codex_home", "platformFamily": "windows", "platformOs": "windows", "userAgent": "Codex Desktop/0.125.0 (Windows 10.0.26200; x86_64) dumb (nexus-codex-runtime-v3-discovery; 0.1.0)"}}`

## Auth Negative Test

- `DUMMY_LOGIN_START_ACCEPTED`: `True`
- Login/start error: `None`
- Login/start response: `{"id": 2, "result": {"type": "chatgptAuthTokens"}}`
- `DUMMY_LOGIN_ACCEPTED_BUT_AUTH_DEPENDENT_OPERATIONS_FAILED`: `BLOCKED_UNKNOWN`
- Dummy `account/read refreshToken=false` OK: `False`
- Dummy `account/read refreshToken=true` OK: `False`
- Dummy `model/list` usable count: `5`
- Dummy `thread/start` succeeded: `True`
- Dummy `turn/start` succeeded: `True`
- Dummy assistant output OK: `False`
- Dummy terminal verdict: `DUMMY_TURN_TIMEOUT_NO_TERMINAL_STATE`
- Dummy terminal observed: `False`
- Phase 1D terminal event handling understood: `False`
- Dummy terminal thread id: `019e5e8a-5024-7012-a41d-33ce7dc7d33d`
- Dummy terminal turn id: `019e5e8a-5036-7031-a704-c04f63057099`
- Dummy turn status: `inProgress`
- Dummy turn error class: `None`
- Dummy turn error message: `None`
- Dummy turn completedAt: `None`
- Dummy turn durationMs: `None`
- Dummy turn items count: `0`
- Dummy cleanup: `{"thread_unsubscribe": {"error": null, "notifications": [{"method": "thread/status/changed", "params": {"status": {"type": "idle"}, "threadId": "019e5e8a-5024-7012-a41d-33ce7dc7d33d"}}, {"method": "turn/completed", "params": {"threadId": "019e5e8a-5024-7012-a41d-33ce7dc7d33d", "turn": {"completedAt": 1779702613, "durationMs": 30131, "error": null, "id": "019e5e8a-5036-7031-a704-c04f63057099", "items": [], "startedAt": 1779702583, "status": "interrupted"}}}], "ok": true, "response": {"id": 322, "result": {"status": "unsubscribed"}}}, "turn_interrupt": {"error": null, "notifications": [], "ok": true, "response": {"id": 321, "result": {}}}}`
- Dummy P0 conditions: `{'dummy_produces_assistant_reply': False, 'dummy_terminal_turn_completed_without_auth_error': False}`

## DUMMY_TOKEN_MATRIX

`{"account_read_refresh_false": "fail", "account_read_refresh_false_reason": "-32600", "account_read_refresh_true": "fail", "account_read_refresh_true_reason": "-32600", "assistant_text_present": "no", "final_verdict": "BLOCKED_UNKNOWN", "local_profile_bypass_detected": "no", "login_start": "success", "model_list": "success", "model_list_count": 5, "terminal_state_observed": "no", "thread_id_present": true, "thread_start": "success", "turn_error_class": "None", "turn_id_present": true, "turn_start": "success", "turn_status": "inProgress"}`

## Local Profile Bypass Test

- OK: `True`
- Skipped: `False`
- Reason: `None`
- `P0_SECURITY_LOCAL_PROFILE_BYPASS`: `False`

## Auth Positive Test

- OK: `False`
- Skipped: `True`
- Reason/Error: `pending_server_credential`
- Token fingerprint: `None`

## VALID_TOKEN_MATRIX

`{"account_read": "pending", "assistant_text_extraction_path": "pending", "credential_available": "no", "login_start": "pending", "model_list": "pending", "strict_json_parse": "pending", "terminal_state_observed": "pending", "thread_start": "pending", "turn_start": "pending"}`

## Conversation Probe

- Skipped: `True`
- Reason: `pending_server_credential`
- Thread start: `None`
- Turn start: `None`
- Assistant extraction OK: `None`
- Extraction path: `None`

## Redaction

- OK: `True`
- Findings: `[]`
- Captured stdout lines: `77`
- Captured stderr lines: `127`

## Sanitized Artifact

- JSON: `C:\Users\Maxim\Documents\nexus\probe_reports\codex_appserver_runtime_v3_discovery\discovery_report_sanitized.json`

No access token, refresh token, bearer token, API key, or JWT-like material is intentionally written to this report.
