# Test Commands — NexusDesk Email Outbound v1.4

## Pack validation

```bash
python 15_automation_scripts/validate_pack.py
```

## Backend core tests

```bash
pytest backend/tests/test_email_runtime_gate.py
pytest backend/tests/test_channel_account_provider_scope.py
pytest backend/tests/test_email_models_migration.py
pytest backend/tests/test_email_admin_api.py
pytest backend/tests/test_email_channel_capabilities.py
pytest backend/tests/test_email_send_schema.py
pytest backend/tests/test_email_outbound_queueing.py
pytest backend/tests/test_email_dispatch_adapter.py
pytest backend/tests/test_email_provider_contract.py
pytest backend/tests/test_email_provider_ses.py
pytest backend/tests/test_email_delivery_events.py
pytest backend/tests/test_email_webhook_auth.py
pytest backend/tests/test_email_inbound_parser.py
pytest backend/tests/test_email_inbound_linking.py
pytest backend/tests/test_email_timeline.py
pytest backend/tests/test_email_observability.py
pytest backend/tests/test_email_suppression.py
```

## Frontend tests

```bash
npm --prefix webapp run typecheck
npm --prefix webapp test
```

## Full smoke evidence after implementation

```bash
BASE_URL=https://your-nexus.example.com \
AUTH_TOKEN=... \
TICKET_ID=... \
EMAIL_ACCOUNT_ID=... \
TEST_RECIPIENT=ops-test@example.com \
bash 15_automation_scripts/smoke_email_full_e2e.sh
```

Optional mock modes in test/staging:

```bash
bash 15_automation_scripts/smoke_email_full_e2e.sh --mock-webhooks
bash 15_automation_scripts/smoke_email_full_e2e.sh --mock-inbound
bash 15_automation_scripts/smoke_email_full_e2e.sh --rollback-check
```
