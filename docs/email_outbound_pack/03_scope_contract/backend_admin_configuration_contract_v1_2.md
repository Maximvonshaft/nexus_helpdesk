# Backend Admin Configuration Contract v1.2

## Configuration split

Production-safe Email configuration is split into two layers.

### Admin UI configurable

These fields can be configured in NexusDesk backend/admin UI:

- Market / global scope.
- Account display name.
- Provider: `ses` only in V1.
- From email.
- From name.
- Reply-To email.
- Return-Path email.
- SES region.
- SES configuration set.
- Secret reference name, not raw secret value.
- Inbound local-part / plus-addressing pattern.
- Account active/inactive state.
- Priority and fallback account.
- Health check status.
- Identity verification status.
- Test-send recipient for verification.

### DevOps/provider controlled

These cannot be solved purely inside NexusDesk UI without unsafe practices:

- Raw AWS access keys or role credentials.
- SPF/DKIM/DMARC DNS records.
- MX records for inbound receiving.
- SES sandbox exit / production sending access.
- SES identity/domain verification at provider level.
- AWS SNS/S3/EventBridge resource creation when infrastructure is not managed by NexusDesk.

The admin UI must surface these as readiness checks and explain what is missing. It must not pretend to complete provider/DNS work if the system cannot verify it.

## Direct backend setup convenience

The system must support a practical first rollout path:

1. DevOps sets fail-closed environment variables and secret references.
2. Admin opens NexusDesk `/accounts` or `/email-accounts` configuration UI.
3. Admin creates or updates Email account metadata.
4. Admin clicks `Check verification`.
5. Admin clicks `Health check`.
6. Admin clicks `Send test email`.
7. Only then can agents send real Email from tickets.

## Validation rules

- `from_email`, `reply_to_email`, `return_path_email`, and test recipient must validate as email addresses.
- CRLF characters are forbidden in all header-derived fields.
- `secret_ref` must match allowlisted naming pattern, e.g. `^[a-zA-Z0-9_.:-]{3,120}$`.
- `provider` must be `ses` in V1.
- `region` must be configured and must match the provider credential scope.
- A configured account is not `ready` until identity is verified and health check succeeds.
