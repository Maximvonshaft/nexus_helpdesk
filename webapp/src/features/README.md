# features layer

The `features` layer owns user-facing business workflows.

Target feature domains:

- workspace
- webchat-admin
- ai-governance
- runtime-control
- channel-accounts
- users-admin
- bulletins

This foundation branch does not move existing route modules yet. Future PRs should extract one feature at a time with smoke evidence.

Allowed dependencies:

- `entities`
- `shared`

Not allowed:

- importing from another feature's private internals
- owning generic primitives that belong in `shared/ui`
- changing backend API contracts without API review
