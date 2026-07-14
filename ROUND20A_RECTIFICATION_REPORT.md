# Round20A Rectification Report

This round is a focused rectification pass based on the Round27 audit.

## Closed items

1. Permission model is now aligned:
   - Sensitive operator pages (`发送线路`, `运营保障`) are restricted to `admin` and `manager`.
   - `lead` no longer sees privileged navigation that the backend would deny.
2. Bulletin center is now closed-loop for customer service:
   - All authenticated users can read markets and bulletins through lookup endpoints.
   - Only `admin` and `manager` can create or edit bulletins.
3. Frontend no longer leaks session key style internals to customer service:
   - `会话编号` is replaced by a business-friendly `来源状态` view.
4. Demo data initialization is now reliable:
   - `init_dev_db.py` commits the created ticket.
   - Default markets and at least one operator bulletin are seeded for demos.

## Verification targets

- Frontend build
- Round20A smoke script
- Round24 hardening tests
- Round27 frontend hardening tests
- Round20A rectification tests
