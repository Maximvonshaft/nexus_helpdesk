# Data Migration Plan

## Migration name

`YYYYMMDD_email_outbound_production`

## New tables

### `email_channel_accounts`

```sql
id
channel_account_id FK channel_accounts.id UNIQUE NOT NULL
provider VARCHAR(40) NOT NULL DEFAULT 'ses'
from_email VARCHAR(255) NOT NULL
from_name VARCHAR(160)
reply_to_email VARCHAR(255)
return_path_email VARCHAR(255)
region VARCHAR(80)
configuration_set VARCHAR(160)
secret_ref VARCHAR(255)
identity_status VARCHAR(40) DEFAULT 'unknown'
is_verified BOOLEAN DEFAULT false
health_status VARCHAR(40) DEFAULT 'unknown'
last_health_check_at TIMESTAMPTZ
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

### `email_outbound_metadata`

```sql
id
outbound_message_id FK ticket_outbound_messages.id UNIQUE NOT NULL
email_channel_account_id FK email_channel_accounts.id
subject TEXT NOT NULL
from_email VARCHAR(255) NOT NULL
from_name VARCHAR(160)
to_email VARCHAR(255) NOT NULL
cc_json TEXT DEFAULT '[]'
bcc_json TEXT DEFAULT '[]'
reply_to_email VARCHAR(255)
return_path_email VARCHAR(255)
message_id_header VARCHAR(255)
in_reply_to VARCHAR(255)
references_header TEXT
text_body TEXT NOT NULL
html_body_sanitized TEXT
provider VARCHAR(40)
provider_message_id VARCHAR(255)
provider_raw_json TEXT
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

### `email_delivery_events`

```sql
id
outbound_message_id FK ticket_outbound_messages.id
provider VARCHAR(40) NOT NULL
provider_event_id VARCHAR(255)
provider_message_id VARCHAR(255)
event_type VARCHAR(80) NOT NULL
recipient_email VARCHAR(255)
diagnostic_code TEXT
raw_payload_json TEXT NOT NULL
occurred_at TIMESTAMPTZ
created_at TIMESTAMPTZ
UNIQUE(provider, provider_event_id)
```

If provider_event_id is missing, dedupe with application hash.

### `email_inbound_messages`

```sql
id
ticket_id FK tickets.id
provider VARCHAR(40)
provider_message_id VARCHAR(255)
message_id_header VARCHAR(255)
in_reply_to VARCHAR(255)
references_header TEXT
from_email VARCHAR(255) NOT NULL
to_email VARCHAR(255) NOT NULL
subject TEXT
text_body TEXT
html_body_sanitized TEXT
raw_storage_key VARCHAR(500)
dedupe_hash VARCHAR(64) UNIQUE NOT NULL
raw_payload_json TEXT
received_at TIMESTAMPTZ
created_at TIMESTAMPTZ
```

### `email_suppression_entries`

```sql
id
email_normalized VARCHAR(255) UNIQUE NOT NULL
reason VARCHAR(80) NOT NULL
provider VARCHAR(40)
provider_event_id VARCHAR(255)
last_event_at TIMESTAMPTZ
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

## Migration rules

- No backfill required.
- Existing outbound rows remain unchanged.
- Existing `channel_accounts` remain unchanged.
- New indexes on recipient, provider ids, ticket ids, status-relevant columns.
- Migration must be safe on PostgreSQL and local SQLite tests where possible.

## v1.1 provider-scope constraints

The migration/model implementation must preserve these invariants:

1. `email_channel_accounts.channel_account_id` must reference a `channel_accounts` row whose `provider` is `email`.
2. The application service must validate this invariant before insert/update even if a DB-level check is not feasible across SQLite/PostgreSQL.
3. Email provider config and secret material must not be stored in `channel_accounts`.
4. `channel_accounts(provider='email')` is only a routing anchor.
5. `email_channel_accounts.secret_ref` is a reference only, never a raw API key.

## v1.1 metadata requirement

`email_outbound_metadata` must be created in the same transaction that queues the `TicketOutboundMessage` for Email.

If metadata creation fails, the outbox row must not be committed.

## v1.1 rollback-safe schema

No migration may make existing `ticket_outbound_messages` rows incompatible.

Do not add non-null columns to existing high-traffic tables unless they have a default and have been tested on PostgreSQL.

## v1.1 dedupe rules

- Delivery events must dedupe by `(provider, provider_event_id)` when event id exists.
- If provider event id is missing, dedupe by a stable SHA-256 hash of canonical provider payload.
- Inbound messages must dedupe by provider message id or canonical raw message hash.
