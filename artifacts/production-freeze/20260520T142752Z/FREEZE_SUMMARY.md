# NexusDesk Production Freeze Evidence

UTC time: 2026-05-20T14:27:52Z
Branch: feat/codex-reply-protocol-discovery-gate
Commit: 2934c6a

## Verified

- Nginx syntax passed.
- Sensitive path hardening snippet is present:
  - /etc/nginx/snippets/nexus_deny_sensitive_paths.conf
- Snippet is included by NexusDesk Nginx site config.
- Public sensitive paths are blocked with 403 or 404.
- Origin-direct sensitive paths are blocked with 403 or 404.
- Business demo paths return 200.
- Public Fast Lane endpoint returns:
  - http_status=200
  - ok=true
  - ai_generated=true
  - reply_source=openclaw_responses
  - ticket_creation_queued=false

## Interpretation

- /wp-config.php returning 403 through Cloudflare/public edge is acceptable.
- Origin returning 404 proves the server-side Nginx snippet is effective.
- The old sensitive-path 200 records in access.log are historical pre-hardening scanner hits.
- Current post-hardening verification passed.

## Evidence location in repo

- artifacts/production-freeze/20260520T142752Z
- deploy/nginx/nexus_deny_sensitive_paths.conf
- deploy/nginx/nexusdesk.site.conf
