# NexusDesk Round B Self-Audit

## Business closure

Implemented as a real flow: widget init → visitor message → Webchat conversation → linked ticket → admin reply → safety gate → visitor polling receives reply.

## Widget backend coupling

The widget calls `/api/webchat/init`, `/api/webchat/conversations/{id}/messages`, and polling endpoints directly. It does not rely on fake local-only state except for browser-side conversation persistence.

## Visitor authorization

Visitor reads and sends require `conversation_id + visitor_token`. The token is stored in the database only as SHA-256 hash.

## Outbound safety

Admin replies call `evaluate_outbound_safety`. The implementation uses `decision.reasons`, not `decision.reason`.

## UI usability

The `/webchat` admin route provides conversation list, thread detail, visitor/source metadata, and a reply composer with evidence and review confirmation toggles.

## PR #7 smoke harness

Round B adds a new smoke file and does not intentionally modify Round A smoke scripts.

## Production config safety

The overlay package does not include or modify:

- `deploy/.env.prod`
- `deploy/docker-compose.server.yml`
- `data/`
- `Dockerfile`

## Secret handling

No secrets, passwords, or real tokens are written into source or docs. Visitor tokens are generated per session and hashed server-side.

## Portability

No absolute production path is hard-coded in application code. The apply script accepts an explicit repo root path.

## Round C readiness

The service layer separates public visitor actions and admin replies, leaving a clean future insertion point for OpenClaw suggested replies with human approval.

## XSS/CORS/token review

- Widget message rendering uses `textContent`, not raw HTML.
- Admin UI displays sanitized text through existing helper functions.
- Visitor token is only used by the visitor API and is not an admin credential.
- CORS is permissive for widget usability in Round B; persistent channel-level domain allowlists are recommended for Round C.

## Logging review

The service avoids logging full visitor message bodies or visitor token values.

## Validation caveat

Syntax checks for generated Python/Bash files were run in the sandbox. Full Alembic, pytest, frontend build, and live smoke must run inside the real repository after applying the overlay because the full runtime is not mounted in this sandbox.
