# Round B Post-Push Audit for Commit 2d53b94

Date: 2026-04-25
Branch: `round-b-webchat-closure`
Audit target: `2d53b94 fix(round-b): make local smoke reproducible and update verification`

## Scope

This audit reviews the post-push follow-up commit `2d53b94` to determine which changes are safe to retain on the formal branch and whether any local smoke convenience changes create production risk.

Files audited in detail:

- `backend/app/api/deps.py`
- `backend/scripts/roundb_ensure_local_admin.py`
- `scripts/smoke/smoke_webchat_round_b.sh`
- `ROUND_B_VERIFY_RESULTS.md`

## Change classification

### A. Must-retain functional fix

#### `scripts/smoke/smoke_webchat_round_b.sh`

Retain.

Reason:
- the original `json_get()` helper was shell-fragile in this environment and failed before the smoke could even run
- the updated helper preserves the original script contract while making JSON extraction actually usable

Effect on production:
- none on production runtime
- affects only the developer smoke script

### B. Local smoke helper only

#### `backend/scripts/roundb_ensure_local_admin.py`

Local validation helper only.

Reason:
- it exists only to ensure a disposable local admin user for a temporary local smoke environment
- it is not referenced by application startup, migrations, worker startup, or deploy compose files

Retention recommendation:
- may remain under `backend/scripts/` if clearly marked development-only
- should never be invoked by production runbooks
- now additionally guarded to exit in `APP_ENV=production`

### C. Potentially sensitive auth path change

#### `backend/app/api/deps.py`

Audited carefully. Retain.

Reason:
- the change is not a new auth bypass
- it only moves `settings = get_settings()` from module-import time to request-time inside `get_current_user()`
- this avoids stale settings capture during local validation and is the more correct dependency behavior

### D. Verification record only

#### `ROUND_B_VERIFY_RESULTS.md`

Retain as documentation of what was actually done and observed.

## Focus audit: `backend/app/api/deps.py`

### Before vs after

Before `2d53b94`:
- `settings = get_settings()` was executed once at module import time
- `get_current_user()` reused that already-cached object

After `2d53b94`:
- module-level `settings = get_settings()` was removed
- `get_current_user()` now calls `settings = get_settings()` at request time

### Does this affect production authentication?

Conclusion: **No production auth expansion was introduced.**

Why:
- `get_current_user()` still checks Bearer token first
- only if no valid bearer user is found does it consider `X-User-Id`
- `X-User-Id` remains gated by `settings.allow_dev_auth`
- `settings.allow_dev_auth` is defined as:

```python
os.getenv("ALLOW_DEV_AUTH", "false").strip().lower() == "true" and self.app_env != "production"
```

So in production:
- even if `ALLOW_DEV_AUTH=true` were set by mistake
- `allow_dev_auth` still resolves to `False` because `app_env == 'production'`

### Will this let `ALLOW_DEV_AUTH` mistakenly work in production?

Conclusion: **No.**

Reason:
- production path is hard-blocked inside settings normalization
- and the allowed environments for dev auth have now been explicitly narrowed to a whitelist: `development`, `test`, and `local`
- `production`, `staging`, `preview`, and `demo` therefore all resolve `allow_dev_auth` to `False` by default

### Does it bypass JWT / current user validation?

Conclusion: **No.**

Reason:
- JWT remains the first path
- no JWT logic was removed
- no user lookup checks were removed
- `is_active` check remains enforced on both token and dev-auth path

### Does it make `X-User-Id` available in production?

Conclusion: **No.**

Reason:
- `X-User-Id` only works when `allow_dev_auth` is true
- `allow_dev_auth` now requires both `ALLOW_DEV_AUTH=true` and `APP_ENV in {development, test, local}`
- therefore `production`, `staging`, `preview`, and `demo` cannot activate `X-User-Id` dev auth by default

### Is it necessary to keep?

Conclusion: **Yes, keeping it is reasonable.**

Reason:
- the request-time fetch avoids stale import-time settings state
- this is safer and more accurate for a dependency that relies on environment-derived config
- the change does not widen production auth behavior

### Final decision on `deps.py`

- **Retain**
- **No rollback required**
- `deps.py` itself remains unchanged in behavior, but the underlying `allow_dev_auth` gate was further narrowed in `settings.py` to an explicit environment whitelist

## Focus audit: `backend/scripts/roundb_ensure_local_admin.py`

### Is it only a local validation helper?

Yes.

### Is it called by production startup?

No evidence of production invocation was found.

It is not part of:
- `app.main`
- Alembic migration chain
- worker scripts
- deploy compose command paths

### Does it write a real password?

No.

It writes only a local development test user with the known disposable dev password `demo123` when missing.

### Should it stay in `scripts/`?

Acceptable to keep temporarily in `backend/scripts/` because it is an execution helper used during repo-local validation.

However, it must be clearly development-only.

### Additional hardening applied

The script now exits immediately in production:

```python
if settings.app_env == 'production':
    raise SystemExit(...)
```

## Focus audit: `scripts/smoke/smoke_webchat_round_b.sh`

### Is the `json_get` fix reasonable?

Yes.

It corrects a shell/Python invocation bug without changing the smoke contract.

### Does it still support `BASE_URL`?

Yes.

### Does it still support `NEXUSDESK_DEV_USER_ID`?

Yes.

The auth selection logic is unchanged:
- prefer `NEXUSDESK_ADMIN_TOKEN`
- else use `NEXUSDESK_DEV_USER_ID`
- else exit 77

### Does it hardcode real credentials or tokens?

No.

### Does it risk polluting production real data?

No inherent production coupling was added.

It still points to the caller-provided `BASE_URL` and uses synthetic smoke data.

## Final audit conclusion

### Classification summary for commit `2d53b94`

- **A. Must retain functional fix**
  - `scripts/smoke/smoke_webchat_round_b.sh`
- **B. Local smoke helper only**
  - `backend/scripts/roundb_ensure_local_admin.py`
- **C. Sensitive path audited, retain**
  - `backend/app/api/deps.py`
- **D. Documentation only**
  - `ROUND_B_VERIFY_RESULTS.md`

### Production risk found?

- `deps.py`: **No production auth expansion found**
- local admin helper: **No production startup integration found**
- smoke script: **No real-secret or production-config contamination found**

### Action taken after audit

- retained `deps.py`
- narrowed `allow_dev_auth` in `settings.py` to `APP_ENV in {development, test, local}` plus explicit truthy `ALLOW_DEV_AUTH`
- hardened `roundb_ensure_local_admin.py` with an explicit production refusal
- recorded this audit in `docs/round-b-post-push-audit.md`

## Result

The branch remains acceptable for `round-b-webchat-closure` and does not require rollback of `deps.py` based on this audit.
