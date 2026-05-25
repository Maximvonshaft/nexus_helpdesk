# Codex App-Server Token Boundary

Nexus Codex Runtime v3 receives request-scoped `chatgptAuthTokens` from the 18794 bridge and injects them into Codex app-server with `account/login/start`.

Boundary rules:

- Token values are never logged, returned, snapshotted, or written to audit payloads.
- Client cache keys use token fingerprints, not raw tokens.
- Each cached app-server client is isolated by tenant, account id, plan type, token fingerprint, model, and runtime start options hash.
- The sidecar clears `CODEX_API_KEY`, `OPENAI_API_KEY`, `OPENAI_ACCESS_TOKEN`, `CODEX_ACCESS_TOKEN`, and `OPENCLAW_HOME` when spawning Codex app-server.
- Local Codex/OpenClaw profiles must not override request-scoped login.
- Dummy token success is a no-go only if it completes an auth-dependent terminal model turn, produces assistant output, bypasses local profile/API-key isolation, or leaks token material.

Failure handling is fail closed. Invalid JSON, auth failures, app-server startup failures, turn timeouts, queue overload, and runtime errors return safe error codes without token material.
