# Round 25 Hardening Report

## Scope
This round is based directly on the original `helpdesk_suite_lite_round24_hardened.zip` source package. The focus is on production-safe hardening with minimal blast radius, plus a precise fix for the AI intake 500 defect.

## Delivered changes

### 1. Fixed deterministic AI intake 500
- Extended `backend/app/schemas.py -> AIIntakeCreate` with optional `market_id` and `country_code`.
- Hardened `backend/app/services/ticket_service.py -> add_ai_intake()` so AI intake records inherit `ticket.market_id` and `ticket.country_code` when callers do not send them explicitly.
- This fixes both:
  - `POST /api/lite/cases/{ticket_id}/ai-intake`
  - `POST /api/tickets/{ticket_id}/ai-intakes`

### 2. Removed Pydantic v2 serialization deprecation
- Replaced deprecated `json_encoders` usage in `APIModel` with a `field_serializer`-based datetime serializer.
- This removes noisy runtime/test warnings and aligns the codebase with Pydantic v2+ best practice.

### 3. Improved OpenClaw MCP observability
- Replaced `stderr=subprocess.DEVNULL` with a managed stderr pipe.
- Added stderr tail capture and structured warning logs through `observability.log_event()`.
- Timeout / early-exit errors now include recent stderr context, which materially improves production debugging.

### 4. Tightened response security headers
- Kept `style-src 'unsafe-inline'` because the React UI uses inline style props in several places.
- Removed `script-src 'unsafe-inline'`.
- Added: `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, and `Permissions-Policy` baseline restrictions.

### 5. Hardened container runtime
- Removed unnecessary `build-essential` from runtime image.
- Added a non-root runtime user.
- Added `/healthz` Docker `HEALTHCHECK`.
- Ensured upload path exists and is owned by the runtime user.
- Tightened `.dockerignore` to keep `.venv`, pytest cache, and built assets out of image context.

### 6. Updated release packaging and deployment metadata
- Bumped app version to `25.0.0`.
- Updated Docker Compose image tags from `round24` to `round25`.
- Updated source release script default artifact name to `helpdesk_suite_lite_round25_source_release.zip`.
- Added this Round 25 report into the release pack list.

## Validation
- `pytest -q`
- `npm run build` inside `webapp/`
- `backend/scripts/build_source_release.sh`

## Notes
- No database migration is required for this round.
- The AI intake fix is source-only and contract-aligned with existing ORM/database fields.
- CSP keeps inline styles enabled intentionally to avoid breaking current UI rendering.
