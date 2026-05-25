# Permission / RBAC Guardrails

## Existing permissions to preserve

- Use existing `ensure_can_send_outbound`.
- Use existing ticket visibility checks.
- Use existing admin restrictions for account management.

## New permissions recommended

If current capability framework supports named overrides, add:

| Capability | Scope |
|---|---|
| `email_account.read` | View email account status. |
| `email_account.write` | Configure email channel accounts. |
| `email_event.read` | View provider delivery events. |
| `email_suppression.write` | Manage suppression entries. |

If named capability changes are too large, restrict new admin endpoints to existing `admin`/`manager` roles.

## Privacy boundary

Agents may see:
- To/From/Subject.
- Delivery status.
- Safe body and timeline.

Agents must not see:
- provider secret refs,
- API keys,
- raw webhook secrets,
- DKIM/private key material.
