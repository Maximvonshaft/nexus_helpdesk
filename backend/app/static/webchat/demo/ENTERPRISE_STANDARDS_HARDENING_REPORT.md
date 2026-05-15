# Enterprise Website Standards Hardening Report

## Scope
This pass upgrades the Speedaf homepage from a strong demo layout toward a production-grade enterprise logistics website.

## Standards applied
- Typography scale: role-based display/headline/body/label hierarchy inspired by Material Design type roles, IBM Carbon type tokens, and Fluent baseline rhythm.
- Spacing system: 4/8/12/16/24/32/48/64 rhythm, aligned with enterprise design-system spacing practices.
- Layout: WebChat remains a global support component rather than a hero-layout object; homepage primary actions remain Track / Ship / Quote.
- Accessibility: target sizes kept at 48px+ for primary controls; anchors normalized for navigation; duplicate IDs checked.
- Enterprise completeness: stronger footer, business/action section, and operational proof points added.

## Changes made
1. Added Inter webfont link with system fallback. No local font files are included.
2. Refined desktop and mobile typography:
   - Reduced excessive headline scale.
   - Improved line-height and letter spacing.
   - Reduced over-heavy weights on navigation, cards, body copy, and buttons.
3. Reduced logo dominance and tightened header rhythm.
4. Converted floating WebChat into a compact enterprise-style circular launcher with hover label.
5. Moved POD card upward to prevent collision with the floating chat launcher.
6. Added enterprise proof strip:
   - 24/7 AI support availability
   - POD evidence-ready delivery
   - SLA operational visibility
   - B2B business customer flow
7. Added shipping / quote / support action section.
8. Expanded footer into enterprise-style footer columns.
9. Normalized all internal anchors:
   - #tracking
   - #ship
   - #services
   - #business
   - #support
   - #quote
10. Rechecked:
   - no duplicate IDs
   - no missing internal anchors
   - no phone-preview duplicated WebChat panel
   - only one real WebChat component remains

## Remaining production items before public launch
1. Replace PNG logo with official SVG logo.
2. Connect API_BASE_URL to the real NexusDesk / WebChat backend.
3. Confirm legal footer URLs: Privacy Policy, Terms of Use, Cookie Policy.
4. Add real company contact address / local support contact if this domain will be public.
5. Decide whether Google-hosted Inter is acceptable, or self-host fonts through your own CDN/legal-approved asset pipeline.
