# Nexus OSR Operator Browser Support

## Certified operator environment

The authenticated Nexus OSR operator console is certified against:

- the Chromium revision pinned by the repository's Playwright version;
- current enterprise Google Chrome releases based on the same Chromium engine;
- current enterprise Microsoft Edge releases based on the same Chromium engine.

Certification covers the canonical authenticated routes, keyboard operation, responsive layouts, target sizes, reduced motion, 200% text enlargement, deterministic visual evidence and representative-volume browser journeys.

## Not represented as certified

Firefox and WebKit/Safari are not represented as certified operator environments until their full browser matrix is added to the canonical acceptance workflow. Unsupported engines must not be silently described as production-qualified.

## Public channel boundary

The customer-side WebChat widget under `backend/app/static/webchat/` is a separate public channel surface with its own compatibility requirements. This document does not narrow that public browser contract.
