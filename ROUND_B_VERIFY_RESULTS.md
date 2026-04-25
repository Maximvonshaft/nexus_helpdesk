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

## Validation summary

### 1. Python compileall

Command actually used:

```bash
python3 -m compileall backend/app backend/scripts
```

Result: **PASS**

Reason: the environment did not provide a `python` command, but `python3` was present and completed successfully.

### 2. Alembic migration

Command:

```bash
cd backend
. .venv/bin/activate
alembic upgrade head
```

Result: **PASS**

The migration `20260425_round_b_webchat` ran successfully.

### 3. Pytest

Command:

```bash
cd backend
. .venv/bin/activate
pytest -q tests/test_outbound_safety.py tests/test_webchat_round_b.py
```

Result: **PASS**

Observed result:

```text
5 passed, 1 skipped in 1.22s
```

### 4. Frontend install, typecheck, build

Commands:

```bash
cd webapp
npm ci
npm run typecheck
npm run build
```

Result: **PASS**

Notes:
- `npm ci` completed successfully
- one moderate vulnerability was reported by npm audit, but it did not block install/build

### 5. Smoke shell syntax

Command:

```bash
bash -n scripts/smoke/*.sh
```

Result: **PASS**

### 6. Live smoke script

Command:

```bash
BASE_URL=http://127.0.0.1:18081 NEXUSDESK_DEV_USER_ID=1 bash scripts/smoke/smoke_webchat_round_b.sh
```

Result: **FAIL (environmental)**

Observed error:

```text
curl: (7) Failed to connect to 127.0.0.1 port 18081 after 0 ms: Could not connect to server
```

Interpretation: the smoke script itself was present and executable, but the NexusDesk service was not running locally on `127.0.0.1:18081` in this environment at validation time.

### 7. Health endpoints

Not executed successfully because the local service was not running on port `18081` after the smoke step failed.

Targets intended by the document:

```bash
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

Current status: **BLOCKED by missing local running service**

## Environment adjustments made to complete validation

The repository environment was missing some runtime tools required by the document commands. The following minimal setup was performed locally in the repo to run honest validation:

- created `backend/.venv`
- installed `backend/requirements.txt`
- installed `pytest`

This was necessary because:

- `python` command was absent, only `python3` existed
- `alembic` was not initially available on PATH
- `pytest` was not initially installed

## Overall conclusion

Status: **Partially validated**

What is green:
- backend Python syntax compilation
- database migration to head
- targeted backend tests
- frontend dependency install
- frontend typecheck
- frontend production build
- smoke script shell syntax

What is not green yet:
- live end-to-end smoke against `http://127.0.0.1:18081`
- `healthz` / `readyz` runtime checks

## Recommended next step

Start the local NexusDesk stack on port `18081`, then rerun:

```bash
BASE_URL=http://127.0.0.1:18081 NEXUSDESK_DEV_USER_ID=1 bash scripts/smoke/smoke_webchat_round_b.sh
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```
