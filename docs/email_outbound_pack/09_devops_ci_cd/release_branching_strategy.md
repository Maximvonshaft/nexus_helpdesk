# Release Branching Strategy

## Feature branch

`feat/email-outbound-production`

## Release branch

Use existing repository convention. If absent:

`release/email-outbound-v1`

## Merge strategy

- Prefer squash merge for feature PRs.
- Keep migration commit visible in PR summary.
- Do not merge directly to production branch without CI and review.

## Tag

Suggested release tag after production validation:

`email-outbound-v1.0.0`
