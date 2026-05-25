# Test Data Management

## Test accounts

Use fake provider for unit/integration tests.

## Staging

Use dedicated staging SES identity:
- `support-staging@<domain>`
- internal recipient mailbox
- bounce simulator address if provider supports it

## Production

Do not use real customer emails for first smoke. Use internal mailbox under production domain.

## Test payloads

Include:
- valid customer email
- invalid email
- suppressed email
- subject with CRLF injection
- HTML with script tag
- reply with In-Reply-To header
- reply using plus-address ticket id
