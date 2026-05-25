# Novice-friendly UX Standard for Email

Email must be usable by a non-technical support agent.

## Principles

1. The agent must know exactly who receives the email.
2. Disabled states must explain what is missing.
3. Provider jargon must be converted into business language.
4. Failure state must include next action.
5. Email delivery states must be distinct from ticket status.

## Required UI labels

| Technical state | Agent-facing copy |
|---|---|
| `email_channel_account` missing | Email account is not configured for this market. |
| `verified_email_identity` missing | Sender email/domain is not verified. |
| `valid_email_recipient` missing | Customer email is missing or invalid. |
| `email_recipient_suppressed` | This email address is blocked because of bounce or complaint. |
| `provider_status=delivered` | Email delivered. |
| `provider_status=bounced` | Email bounced. Check customer email address. |
| `provider_status=complaint` | Customer marked email as spam. Do not resend without review. |

## Layout

Email compose area must show:
- From
- To
- Subject
- Body
- Send button
- Disabled reason, if not sendable

Do not hide missing requirements behind generic toasts.
