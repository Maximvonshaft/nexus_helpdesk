# Overlay Kit limitation note

This kit is an **honest overlay deliverable** built from the current GitHub `main` code surface and the prior governance kits.

What it includes:
- direct overlay files for high-confidence frontend and shared-governance paths;
- a real Alembic revision with a non-placeholder `down_revision`;
- a stronger `patch.diff` for the larger backend files that still need to be merged inside a real local checkout.

Why this shape:
- the execution environment can read GitHub through the connector,
- but cannot reliably materialize a full local working copy of the repository,
- so the safest non-fabricated output is: **overlay files for the parts we can replace confidently + a detailed patch reference for the core backend files**.

Recommended usage:
1. apply `overlay/` into a real local checkout;
2. inspect and merge `patch.diff` for the backend core files;
3. run Alembic, backend smoke, frontend build/lint/typecheck in that real checkout.
