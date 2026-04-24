# Backend Module Split Plan

`backend/app/api/admin.py` should be split gradually. This patch does not force a risky router rewrite because preserving API compatibility is more important than cosmetic structure.

Safe sequence:

1. Extract pure serializers/helpers.
2. Extract user routes.
3. Extract market/channel routes.
4. Extract AI config routes.
5. Extract OpenClaw runtime routes.
6. Keep `admin.py` as aggregate compatibility router.
