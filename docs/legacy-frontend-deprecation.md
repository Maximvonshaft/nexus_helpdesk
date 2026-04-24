# Legacy Frontend Deprecation

`webapp/` is the current frontend source of truth.

`frontend/` is a fallback only. Do not add new product features to `frontend/`.

Deletion conditions:

1. React webapp passes production smoke test.
2. Runtime and Workspace pages are accepted by operations.
3. Deployment no longer depends on legacy files.
4. One release cycle completes without fallback usage.
