# chatgptAuthTokens Login Payload Builder

The Codex App Server adapter transforms a database-stored Codex credential into the `chatgptAuthTokens` payload expected by the private App Server bridge.

Format:
```json
{
  "type": "chatgptAuthTokens",
  "accessToken": "...",
  "chatgptAccountId": "...",
  "chatgptPlanType": "..."
}
```
This payload is injected securely during the generation request to the bridge, bypassing the need for a persistent file-based session.
