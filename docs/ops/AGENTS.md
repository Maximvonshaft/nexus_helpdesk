# docs/ops/AGENTS.md — Operations Runbook Execution Contract

This contract applies to `docs/ops/**`. Ops documents are used during deployment, recovery, rollback, provider runtime validation, and incident handling. They must be executable, evidence-based, and safe.

## 1. Runbook quality bar

Every runbook should include:

```text
purpose
scope
affected services
preconditions
required permissions
required secrets without exposing values
commands
expected output
failure classification
rollback path
post-checks
owner/operator handoff notes
```

Do not write vague prose where a command, file path, endpoint, or log pattern is required.

## 2. Command style

Commands must be copy-pasteable and safe by default:

```bash
set -Eeuo pipefail
```

Avoid destructive commands unless clearly marked and gated.

Prefer read-only diagnostics first:

```text
healthz/readyz
container status
logs tail
git status
docker compose config
alembic current/heads
curl status endpoints
```

## 3. Secret handling

Runbooks must not contain:

```text
real tokens
real passwords
private cookies
private session data
real OAuth authorization responses
private gateway credentials
```

Use placeholders:

```text
$NEXUS_ADMIN_TOKEN
$CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN
/run/secrets/<name>
```

Never instruct operators to paste secrets into logs, screenshots, PRs, or chat transcripts.

## 4. Provider runtime runbooks

Codex/OpenClaw provider runbooks must preserve:

```text
reply-only Codex boundary
NexusDesk as policy/audit gate
private upstream URL guard
strict JSON contract
fallback provider path
kill switch / canary rollout
secret redaction
safe rollback to OpenClaw/rule_engine/safe_ack
```

Any rollout runbook must define stages:

```text
status surface check
contract fixture check
private upstream check
staging canary
limited production canary
full rollout
rollback trigger
```

## 5. Deployment runbooks

Deployment runbooks must separate:

```text
source pull/build
secret/env preparation
backup
migration
container rollout
health checks
Nginx/proxy checks
smoke tests
log checks
rollback
```

Never instruct `git reset --hard` or cleanup against a live server directory until these paths are backed up and intentionally restored:

```text
deploy/.env.prod
data/
uploaded attachments
local storage roots
server-only compose override
private proxy/TLS files
```

## 6. Rollback runbooks

Rollback instructions must state whether rollback is:

```text
code-only
config-only
image tag rollback
feature flag rollback
database forward-fix
restore from backup
not safely reversible
```

If a migration is not safely downgradable, say so directly and provide the safest forward-fix/restore option.

## 7. Verification evidence

Every runbook should specify evidence artifacts:

```text
command output file
log file path
health endpoint result
ready endpoint result
CI run link if applicable
artifact name
operator screenshot only when needed and no secrets visible
```

## 8. Incident language

Use exact classifications:

```text
config_error
secret_missing
provider_unavailable
upstream_timeout
schema_mismatch
migration_failed
permission_denied
rate_limited
queue_backlog
daemon_down
unsafe_to_continue
```

Avoid ambiguous terms like `broken` without root cause evidence.
