# Task Breakdown WBS — Navigation Only

## Important v1.4 notice

This file is retained only as a high-level navigation map.

For execution, do **not** use this file as the source of truth.

The v1.4 execution source of truth is:

```text
06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv
```

That board contains issue/PR-level atomic tasks with owner role, dependencies, PR slice, exact code requirement, test command, expected evidence, rollback impact, and merge independence.

---

# Task Breakdown / WBS

## WBS-1 — Backend foundation

- [ ] Add settings.
- [ ] Add models.
- [ ] Add migration.
- [ ] Add schemas.
- [ ] Add capability logic.
- [ ] Add tests.

## WBS-2 — Outbound send

- [ ] Add provider base contract.
- [ ] Add SES provider.
- [ ] Add email adapter.
- [ ] Integrate with `message_dispatch`.
- [ ] Create metadata on send.
- [ ] Add route/provider/failure tests.

## WBS-3 — Delivery events

- [ ] Add event parser.
- [ ] Add webhook endpoint.
- [ ] Add suppression update.
- [ ] Add ticket event/timeline logging.
- [ ] Add idempotency tests.

## WBS-4 — Inbound replies

- [ ] Add inbound parser.
- [ ] Add ticket matching logic.
- [ ] Add inbound API endpoint.
- [ ] Add unresolved handling.
- [ ] Add parser/linking tests.

## WBS-5 — Frontend

- [ ] Update types.
- [ ] Update API client.
- [ ] Add email compose UI.
- [ ] Add disabled reasons.
- [ ] Add timeline display states.
- [ ] Add admin account UI if in scope.

## WBS-6 — Release

- [ ] Add runbooks.
- [ ] Add env example.
- [ ] Run tests.
- [ ] Staging smoke.
- [ ] Production go/no-go.
