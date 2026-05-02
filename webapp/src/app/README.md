# app layer

The `app` layer owns application bootstrapping, providers, router assembly, shell wiring, and global boundaries.

This initial foundation commit is intentionally behavior-neutral. Existing runtime code remains in its current locations until later reviewed migration PRs.

Allowed responsibilities:

- app providers
- router wiring
- global shell composition
- app-level error/loading boundaries
- bootstrap-only code

Not allowed:

- feature business logic
- domain model code
- direct backend API implementation details
- WebChat visitor widget runtime code
