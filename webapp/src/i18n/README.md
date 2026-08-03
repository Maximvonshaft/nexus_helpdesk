# Operator UI localization authority

This directory owns the Nexus operator-interface locale. It does not own customer conversation language, knowledge-document locale, AI reply language, or source-authored evidence.

## Boundaries

The Vite presentation transform localizes repository-authored static copy only. Runtime values such as customer messages, operator notes, ticket descriptions, knowledge bodies, evidence payloads, and audit content remain verbatim.

Technical control-flow values used in equality checks, switch clauses, object keys, imports, and type positions are not transformed.

Each candidate message receives an occurrence-scoped stable key derived from its source file, expression kind, Chinese fallback text, and same-message ordinal. Catalogs resolve by that key rather than by the Chinese sentence. Identical text used in a visible label and in a business payload therefore cannot accidentally share a translation.

## Production locales

The supported operator locales are:

- `zh-CN` — Simplified Chinese source and safe fallback;
- `en` — English UI with `en-GB` date and number formatting;
- `de` — German UI with `de-DE` date and number formatting;
- `cnr` — Montenegrin UI in Latin script, with `sr-Latn-ME` used only as the JavaScript Intl formatting locale.

Browser values `cnr`, `cnr-ME`, `sr-ME` and `sr-Latn-ME` normalize to the one product locale `cnr`. The user-facing name is `Crnogorski`. Nexus does not create a parallel Serbian product locale.

The locale bootstrap resolves one locale before importing any application module. It loads only that locale's same-origin static catalog, then initializes the synchronous i18next runtime and the React application. This ordering guarantees that module-level labels and navigation maps use the selected locale on first execution without adding all catalogs to the first-screen JavaScript bundle.

A missing English, German or Montenegrin catalog blocks application startup with a localized recovery surface. Nexus never marks an English, German or Montenegrin document while rendering Chinese fallback copy. The operator can retry or use a session-scoped Chinese recovery view to reach Account settings and repair the persisted preference.

Montenegrin MUI copy is maintained as a Nexus-owned `cnr` locale. Serbian Latin copy is not reused unchanged: product terms such as `sljedeću`, `posljednju` and `Zvijezda` are explicitly governed as Montenegrin.

## Preference authority

Anonymous entry surfaces use the device preference stored in local browser storage. After authentication, the server-owned `user_ui_preferences` row is authoritative. An account without an explicit preference adopts the current device/login locale exactly once; subsequent sessions use the saved account preference.

Changing the UI locale does not modify the User identity version and therefore does not revoke a valid authentication session. Password, MFA and permission changes remain governed by their existing identity-version authority.

## Catalog release policy

The production build emits `i18n-inventory.json`. English, German and Montenegrin catalogs must contain exactly one non-empty value for every inventory key and no extra keys. The catalog gate rejects source-language residue, placeholder changes, generation markers, repeated garbage and abnormal length expansion.

Structural completeness is not semantic approval. Catalog candidates are generated only in an isolated tooling workflow, with model IDs, resolved revisions, approved licenses, fallback counts, override digests and output SHA-256 values recorded. High-risk identity, security, logistics, routing, provider and customer-support terms use reviewed product-language overrides.

Montenegrin candidates are produced in Latin script from the reviewed English semantic layer, then normalized and reviewed against Montenegrin product terminology. A South Slavic or Serbian-form candidate is not automatically accepted as Montenegrin.

Browser acceptance covers anonymous login in all non-Chinese locales, authenticated preference persistence, German and Montenegrin narrow layouts, safe catalog failure, native Montenegrin MUI copy and source-authored evidence preservation.

Any source inventory, runtime dependency, model revision, model license, terminology override or catalog content change invalidates the prior catalog evidence and requires a new exact-Head review and Canonical Acceptance.
