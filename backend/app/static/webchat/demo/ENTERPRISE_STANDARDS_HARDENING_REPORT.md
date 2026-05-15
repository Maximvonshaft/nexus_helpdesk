# Speedaf Showcase Hardening Report

## Scope

This static showcase page replaces the old `/webchat/demo.html` visual shell with a Speedaf-branded logistics homepage and a customer-facing support panel.

## Current integration status

- Served from `/webchat/demo/` under the existing NexusDesk webchat static mount.
- `/webchat/demo.html` redirects to `/webchat/demo/` through plain HTML meta refresh.
- The showcase chat posts customer messages to `POST /api/webchat/fast-reply`.
- The active frontend payload is schema-safe: no `visitor.source`, no `recent_context[].content`, and no unsupported backend fields.
- Customer-visible bot replies require `ok=true` and a non-empty backend `reply`.
- API failures, invalid responses, empty replies, and timeouts display only `Connection issue. Please try again.`

## Visual hardening applied

- Speedaf-branded hero, navigation, tracking entry, support panel, proof cards, services, business section, and footer.
- System font stack only; no external Google Fonts dependency.
- Single customer-facing support surface: floating chat launcher to popup panel.
- Tracking form opens support chat and sends the tracking number to the real Fast Reply endpoint.
- Quick actions send real customer messages to the Fast Reply endpoint.
- Static fake parcel-status claims and fake voice support entry points were removed.

## Remaining checks before production showcase

- Validate the deployed runtime with a browser Network capture showing `POST /api/webchat/fast-reply`, response `ok=true`, and non-empty `reply`.
- Validate `WEBCHAT_ALLOWED_ORIGINS` for the actual public demo domain.
- Confirm legal footer URLs and company contact details before public launch.
