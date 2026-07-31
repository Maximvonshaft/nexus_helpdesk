# Operator UI localization authority

This directory owns the Nexus operator-interface locale. It does not own customer conversation language, knowledge-document locale, AI reply language, or source-authored evidence.

## Boundaries

The Vite presentation transform localizes repository-authored static copy only. Runtime values such as customer messages, operator notes, ticket descriptions, knowledge bodies, evidence payloads, and audit content remain verbatim.

Technical control-flow values used in equality checks, switch clauses, object keys, imports, and type positions are not transformed.

Each candidate message receives an occurrence-scoped stable key derived from its source file, expression kind, Chinese fallback text, and same-message ordinal. Catalogs resolve by that key rather than by the Chinese sentence. Identical text used in a visible label and in a business payload therefore cannot accidentally share a translation.

## Delivery state

PR1 enables only `zh-CN` and must remain visually and behaviorally equivalent to the existing application. The production build emits `i18n-inventory.json`, which becomes the authoritative input for complete English and German catalogs and for the explicit review of non-presentation occurrences.

English and German must not be enabled until catalog completeness, layout coverage, browser journeys, account preference persistence, and final Canonical Acceptance are all proven.
