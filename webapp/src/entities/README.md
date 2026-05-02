# entities layer

The `entities` layer owns reusable domain models and domain-specific API/query abstractions.

Target entity domains:

- ticket
- conversation
- customer
- channel
- ai-config
- user
- runtime

This foundation branch is behavior-neutral and does not move current API methods yet.

Allowed dependencies:

- `shared`

Not allowed:

- importing from `features`
- owning page layout
- owning app shell code
- mutating public/admin API contracts without review
