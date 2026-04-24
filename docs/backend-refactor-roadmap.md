# Backend Refactor Roadmap

## Admin router split

Target files:

- `admin_users.py`
- `admin_markets.py`
- `admin_channel_accounts.py`
- `admin_ai_configs.py`
- `admin_openclaw.py`
- `admin_runtime.py`

Keep API paths unchanged.

## Model split

Target packages:

- `models/user.py`
- `models/ticket.py`
- `models/openclaw.py`
- `models/integration.py`
- `models/ai_config.py`
- `models/ops.py`

## Transaction boundary

- API/request path owns commit via `managed_session`.
- Normal services should use `flush`, not `commit`.
- Queue claim functions may commit independently, but only with an explicit comment.
