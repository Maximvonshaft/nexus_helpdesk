# Helpdesk Suite Lite — Round 24 hardening report

## Objective
This round closed the remaining engineering-governance issues identified in the independent Round 23 audit. The focus was not new feature work; it was release hygiene, test portability, dependency consistency, and reproducible delivery.

## Changes implemented

### 1. Test portability and validation-chain hardening
- Renamed the regression suite to `backend/tests/test_round24_hardening.py` so the package no longer carries stale round naming.
- Removed the hard-coded `.venv/bin/alembic` dependency from the Alembic integration test.
- The suite now discovers Alembic portably by preferring `alembic` from `PATH` and otherwise falling back to `sys.executable -m alembic`.
- Added release-governance assertions:
  - source-release script produces a clean zip
  - release zip contains required deployment assets
  - release zip excludes runtime DB and cache artifacts
  - `prometheus-client` is declared in requirements
  - compose image tags are aligned to Round 24

### 2. Smoke verification now exercises the real migration chain
- Updated `backend/scripts/smoke_verify_round15.py` to bootstrap schema with `alembic upgrade head` instead of `Base.metadata.create_all()`.
- This makes the smoke path validate the actual migration chain and reduces the chance of schema drift hiding behind ORM metadata creation.

### 3. Dependency declaration consistency
- Added `prometheus-client==0.21.1` to `backend/requirements.txt`.
- Removed the special-case `pip install prometheus-client` line from `Dockerfile`.
- Production dependencies are now declared in one authoritative place instead of being split between requirements and image-only post-installs.

### 4. Release and deployment version alignment
- Updated `deploy/docker-compose.cloud.yml` so application images consistently reference `nexusdesk/helpdesk:round24`.
- Updated `backend/app/main.py` application version from `23.0.0` to `24.0.0`.
- Updated the README status heading to `Round 24 status`.

### 5. Reproducible source-release packaging
- Rewrote `backend/scripts/build_source_release.sh` so it packages the real current delivery surface:
  - `backend/`
  - `frontend/`
  - `webapp/`
  - `deploy/`
  - `scripts/`
  - root `Dockerfile`, `README.md`, `.dockerignore`
- The release script now excludes runtime and build garbage consistently:
  - `*.db`, `*.sqlite`
  - `__pycache__/`, `.pytest_cache/`
  - `node_modules/`, `dist/`, `coverage/`
  - `uploads/`
  - `tsconfig.tsbuildinfo`
- Removed `backend/helpdesk.db` from the source tree before repackaging so the shipped source release is clean.

## Files materially changed
- `backend/tests/test_round24_hardening.py`
- `backend/scripts/smoke_verify_round15.py`
- `backend/scripts/build_source_release.sh`
- `backend/requirements.txt`
- `Dockerfile`
- `deploy/docker-compose.cloud.yml`
- `backend/app/main.py`
- `README.md`
- removed: `backend/helpdesk.db`

## Validation performed

### Python compile pass
- `python -m compileall backend/app backend/scripts backend/alembic/versions`

### Regression suite
- `pytest -q backend/tests/test_round24_hardening.py -q`
- Result: **20/20 passed**

### Alembic
- `alembic -c backend/alembic.ini heads`
  - Result: **single head: `20260410_0011`**
- `alembic -c backend/alembic.ini upgrade head`
  - Result: **passed**
- `alembic -c backend/alembic.ini check`
  - Result: **No new upgrade operations detected.**

### Smoke verification
- `python backend/scripts/smoke_verify_round15.py`
- Result: **ROUND15_SMOKE_PASSED**

### Front-end reproducible install and production build
- `cd webapp && npm ci`
- `cd webapp && npm run build`
- Result: **passed**

### Source-release verification
- `bash backend/scripts/build_source_release.sh /tmp/.../release.zip`
- Verified inside the generated zip:
  - required files present: `backend/requirements.txt`, `deploy/docker-compose.cloud.yml`, `webapp/package-lock.json`, `Dockerfile`
  - runtime DB absent: `backend/helpdesk.db`
  - cache/build trash absent: `__pycache__/`, `*.pyc`

## Release posture
The remaining issues called out in the Round 23 audit have been materially closed in source.

Current posture:
- Core runtime and migration chain: **stable**
- Test portability and credibility: **improved and repeatable**
- Dependency declaration consistency: **aligned**
- Release packaging cleanliness: **aligned**
- Front-end install/build reproducibility: **verified**

## Known non-blocking note
The regression suite still emits Pydantic deprecation warnings related to `json_encoders`. They do not block runtime or deployment, but they should be cleaned up in a future round before Pydantic v3 migration work.
