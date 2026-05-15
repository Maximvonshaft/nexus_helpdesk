# Speedaf Showcase UI Refactor Report

## Objective

Replace the old minimal WebChat demo page with a Speedaf-branded showcase page that is suitable for visual demonstration while preserving a real NexusDesk WebChat Fast Lane integration.

## Key decisions

1. The old `/webchat/demo.html` page now redirects to `/webchat/demo/`.
2. The showcase page uses its own polished Speedaf UI instead of the old plain demo shell.
3. The customer-facing chat panel posts to `POST /api/webchat/fast-reply`.
4. Quick actions send real user messages to the backend instead of displaying local bot replies.
5. The tracking form opens chat and sends `Track parcel {tracking_number}` to the backend.
6. Local fallback tracking answers, fake shipment status, fake handoff messages, and fake voice support were removed.
7. The browser displays backend replies only after strict response validation.

## Layout

- Header: Track, Ship, Services, Business, Support, Login, Get a quote.
- Hero: tracking entry and logistics visual.
- Trust strip: tracking support, proof of delivery, AI plus human support.
- Services section: cross-border parcels, last-mile delivery, business support.
- Footer: shipment, service, support, and company links.
- WebChat: one floating launcher and one popup panel.

## Required runtime validation

Before presenting the branch as production-ready, validate against the deployed server:

- `/webchat/demo.html` loads or redirects correctly.
- `/webchat/demo/` loads the showcase page.
- `/webchat/demo/js/app.js` is served.
- Browser Network shows `POST /api/webchat/fast-reply`.
- Request payload has no `visitor.source` and no `recent_context[].content`.
- Response has `ok=true` and non-empty `reply`.
- Failure cases display only `Connection issue. Please try again.`

## Remaining production notes

- Confirm `WEBCHAT_ALLOWED_ORIGINS` for the public demo domain.
- Add real legal footer URLs before public launch.
- Add real corporate contact details before public launch.
