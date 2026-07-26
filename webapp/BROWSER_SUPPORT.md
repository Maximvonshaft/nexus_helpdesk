# Nexus OSR Operator Browser Support

## Certified operator environment

The authenticated Nexus OSR operator console is certified against:

- the Chromium revision pinned by the repository's Playwright version;
- current enterprise Google Chrome releases based on the same Chromium engine;
- current enterprise Microsoft Edge releases based on the same Chromium engine.

Certification covers the canonical authenticated routes and requires exact-Head evidence for:

- semantic landmarks, accessible names and natural keyboard order;
- visible keyboard focus, including Windows Forced Colors presentation;
- independently operable controls with a 44 CSS-pixel target floor;
- normal-text and large-text WCAG AA contrast thresholds;
- reduced-motion operation;
- structural reflow at the 320 CSS-pixel release floor;
- 200% text enlargement without label/value overlap, clipping, hidden actions or root horizontal overflow;
- executable loading, empty, degraded, conflict, repair-required and enlarged-text state assertions over DOM, geometry, contrast, focus and task behavior;
- representative-volume queue and conversation journeys.

The canonical Playwright suite is the release authority for browser behavior. Screenshots may be captured during diagnosis or controlled human review, but screenshot files, image hashes and pixel baselines are not a parallel acceptance path and cannot replace executable assertions.

Viewport width alone is not the responsive authority. The shell and Workspace consume the same layout mode derived from both viewport capacity and effective root text scale, so enlarged text cannot leave the application in a compressed desktop layout.

## Not represented as certified

Firefox and WebKit/Safari are not represented as certified operator environments until their full browser matrix is added to the canonical acceptance workflow. Unsupported engines must not be silently described as production-qualified.

Automated browser evidence does not replace assistive-technology acceptance in a controlled deployment environment. Release sign-off should include the organization's supported Windows screen-reader and browser combination when that environment is available.

## Public channel boundary

The customer-side WebChat widget under `backend/app/static/webchat/` is a separate public channel surface with its own compatibility requirements. This document does not narrow that public browser contract.
