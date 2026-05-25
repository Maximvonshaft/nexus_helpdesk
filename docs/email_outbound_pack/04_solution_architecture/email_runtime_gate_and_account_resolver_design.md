# Email Runtime Gate and Provider-Scoped Resolver Design

## Objective

Prevent Email implementation from breaking existing WhatsApp/Telegram/SMS/WebChat channels while making Email a first-class outbound adapter.

## Channel classification

Do not use one set for all purposes.

### Semantic external channels

Used for UI/timeline classification:

```python
{whatsapp, telegram, sms, email}
```

### Worker-eligible channels

Computed at runtime:

```python
eligible = set()

if ENABLE_OUTBOUND_DISPATCH:
    if OUTBOUND_PROVIDER == "openclaw":
        eligible |= {whatsapp, telegram, sms}
    if OUTBOUND_EMAIL_ENABLED and EMAIL_PROVIDER == "ses":
        eligible |= {email}
```

`claim_pending_messages(...)` must use worker-eligible channels, not the semantic external set.

## Safe processing fallback

If `process_outbound_message(...)` receives an Email row while Email is disabled:

```text
status -> pending
provider_status -> email_dispatch_paused
locked_at -> null
locked_by -> null
retry_count -> unchanged
next_retry_at -> null or future safe retry
```

It must not call `_mark_retry` and must not call `_mark_dead`.

## Provider-scoped account resolver

Required API:

```python
def resolve_channel_account_for_provider(
    db: Session,
    *,
    provider: str,
    market_id: int | None,
    account_id: str | None = None,
) -> ChannelAccount | None:
    provider = provider.strip().lower()
    ...
```

Pseudo logic:

```python
q = db.query(ChannelAccount).filter(
    ChannelAccount.provider == provider,
    ChannelAccount.is_active.is_(True),
)

if account_id:
    return q.filter(ChannelAccount.account_id == account_id).first()

if market_id is not None:
    row = q.filter(ChannelAccount.market_id == market_id).order_by(...).first()
    if row:
        return row

return q.filter(ChannelAccount.market_id.is_(None)).order_by(...).first()
```

## Admin provider validation

Do not reuse an OpenClaw-only constant for all channel-account governance.

Create:

```python
OPENCLAW_CHANNEL_ACCOUNT_PROVIDERS = {"whatsapp", "telegram", "sms"}
CUSTOMER_CHANNEL_ACCOUNT_PROVIDERS = {"whatsapp", "telegram", "sms", "email"}
EMAIL_CHANNEL_ACCOUNT_PROVIDERS = {"email"}
```

Admin creation may allow `email`, but only if the Email companion account record is created/validated.

## Email account companion table

`ChannelAccount(provider="email")` is only the routing anchor.

Production Email configuration belongs to:

```text
email_channel_accounts
```

`email_channel_accounts.channel_account_id` must point to a `ChannelAccount` whose provider is `email`.

Enforce in application logic and tests. Use DB check if feasible without hurting SQLite test compatibility.

## Rollback invariant

Email-only rollback must satisfy:

```text
No new email provider calls.
No pending email rows marked dead because of feature disable.
No non-email channel stopped if global outbound remains enabled.
```
