# Operator UI localization authority

This directory owns the Nexus operator-interface locale. It does not own customer conversation language, knowledge-document locale, AI reply language, or source-authored evidence.

## Boundaries

The Vite presentation transform localizes repository-authored static copy only. Runtime values such as customer messages, operator notes, ticket descriptions, knowledge bodies, evidence payloads, and audit content remain verbatim.

Technical control-flow values used in equality checks, switch clauses, object keys, imports, and type positions are not transformed.

## Delivery state

PR1 enables only `zh-CN` and must remain visually and behaviorally equivalent to the existing application. The production build emits `i18n-inventory.json`, which becomes the authoritative input for complete English and German catalogs.

English and German must not be enabled until catalog completeness, layout coverage, browser journeys, account preference persistence, and final Canonical Acceptance are all proven.
