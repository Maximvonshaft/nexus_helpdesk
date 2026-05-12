# NexusDesk Email Provider

This P0 closure branch implements only a local sandbox provider.

## Provider

`SandboxEmailProvider` returns deterministic provider results and performs no network call.

Success result:

```text
provider=sandbox_email
provider_message_id=sandbox_email_<outbound_message_id>
provider_thread_id=sandbox_thread_<ticket_id>
status=sent
```

Forced failure:

If the outbound body contains `SANDBOX_FAIL`, sandbox provider returns:

```text
status=failed
error_code=sandbox_failure
error_message=Sandbox forced failure
provider_message_id=None
```

## Boundary

- No SMTP is configured.
- No SES/SendGrid/Mailgun is configured.
- No real email is sent.
- Email outbound must not use OpenClaw.

Real provider support should be added in a separate PR after this P0 closure is accepted.
