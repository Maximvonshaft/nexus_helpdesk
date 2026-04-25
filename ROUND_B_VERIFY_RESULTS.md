# Round B Verification Results

Date: 2026-04-25
Branch: `round-b-webchat-closure`
Repo: `Maximvonshaft/nexus_helpdesk`
Applied from: `nexusdesk-round-b-webchat-closure.zip`

## Apply result

Overlay files were written into the current branch from the ZIP contents.

Protected production paths were not overwritten:

- `deploy/.env.prod`
- `deploy/docker-compose.server.yml`
- `data/`
- server-local `Dockerfile` differences

Note: the ZIP contained `webapp/src/lib/types.roundb.snippet.ts` instead of `webapp/src/lib/webchatTypes.ts` listed in `ROUND_B_MOBILE_APPLY.md`. To preserve compatibility with the current codebase, the snippet types were appended into `webapp/src/lib/types.ts` and the overlay `webapp/src/lib/api.ts` was applied as provided.

## Local validation environment used

To complete the live Round B smoke without touching production files or server config, a temporary local validation environment was started inside the local clone:

- backend served locally on `127.0.0.1:18081`
- local SQLite database path under the repo backend working tree
- temporary dev-style environment variables only
- no changes to `deploy/.env.prod`
- no changes to `deploy/docker-compose.server.yml`
- no writes to production secrets, tokens, or passwords

Chosen path:
- did **not** use the repo Docker Compose stack for this validation
- used the backend virtualenv plus local `uvicorn` for the minimal reproducible smoke environment

Reason:
- the smoke only required a working local API surface and admin auth path
- this was the smallest, lowest-risk path that avoided unrelated OpenClaw/Postgres/runtime variables

## Validation summary

### 1. Python compileall

Command used:

```bash
python3 -m compileall backend/app backend/scripts
```

Result: **PASS**

### 2. Alembic migration

Command used:

```bash
cd backend
.venv/bin/alembic upgrade head
```

Result: **PASS**

The migration `20260425_round_b_webchat` ran successfully.

### 3. Pytest

Command used:

```bash
cd backend
.venv/bin/pytest -q tests/test_outbound_safety.py tests/test_webchat_round_b.py
```

Result: **PASS**

Observed result:

```text
5 passed, 1 skipped
```

### 4. Frontend typecheck and build

Commands used:

```bash
cd webapp
npm run typecheck
npm run build
```

Result: **PASS**

### 5. Smoke shell syntax

Command used:

```bash
bash -n scripts/smoke/*.sh
```

Result: **PASS**

### 6. Health endpoints

Commands used:

```bash
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

Result: **PASS**

Observed responses:

```json
{"status":"ok","env":"development"}
{"status":"ready","database":"ok"}
```

### 7. Live Round B smoke

Command used for final passing run:

```bash
ADMIN_TOKEN=$(curl -fsS -X POST http://127.0.0.1:18081/api/auth/login \
  -H 'Content-Type: application/json' \
  --data '{"username":"admin","password":"demo123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
BASE_URL=http://127.0.0.1:18081 NEXUSDESK_ADMIN_TOKEN="$ADMIN_TOKEN" \
  bash scripts/smoke/smoke_webchat_round_b.sh
```

Result: **PASS**

Observed smoke flow:

```text
PASS init ...
PASS visitor send
PASS visitor poll inbound
PASS ticket ...
PASS safety block
PASS admin reply
PASS visitor sees reply
PASS Round B webchat closure smoke
```

## Minimal local fixes required during validation

Two local issues were discovered while making the smoke actually runnable in this environment.

### A. Smoke script JSON helper bug

File:
- `scripts/smoke/smoke_webchat_round_b.sh`

Issue:
- the original `json_get()` helper used an inline `python3 -c` string with escaped newlines that failed in this shell environment
- then a here-doc approach was tested, but that consumed stdin and broke the input pipeline

Fix:
- replaced `json_get()` with a minimal environment-variable based JSON parsing helper that works reliably with piped payloads in this environment

Reason:
- required for the smoke script itself to execute honestly
- no production behavior changed, only local smoke script correctness improved

### B. Local admin authentication for smoke

Issue:
- the local SQLite state did not contain the expected `id=1` dev user assumed by `NEXUSDESK_DEV_USER_ID=1`
- therefore the safer and more deterministic route for local smoke was to authenticate through the standard login endpoint

Local support action:
- added `backend/scripts/roundb_ensure_local_admin.py` to ensure a local-only admin user exists for validation
- this created a local test admin user (`admin`) with the seeded dev password (`demo123`) when missing

Reason:
- needed only for the temporary local validation environment
- does not touch production configuration or secrets

## Overall conclusion

Status: **GREEN for local Round B validation**

What is verified:
- backend source compiles
- migration chain reaches head
- targeted backend tests pass
- frontend typecheck passes
- frontend production build passes
- smoke shell scripts are syntactically valid
- local service starts cleanly on `127.0.0.1:18081`
- `/healthz` and `/readyz` pass
- full Round B live smoke passes end-to-end

## Ready state

The branch is ready to push as `round-b-webchat-closure`.
