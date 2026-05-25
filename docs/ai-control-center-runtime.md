# AI Control Center Runtime

NexusDesk now uses the first-class control-plane models for daily AI operations:

`PersonaProfile -> KnowledgeItem upload -> parsing -> KnowledgeChunk indexing -> metadata-filtered retrieval -> ProviderRequest.metadata -> Codex appserver runtime`

## Admin Workflow

- `/ai-control` is the daily AI Control Center for Persona and Knowledge Base work.
- `/control-plane` remains a read-only governance overview.
- Persona drafts do not affect runtime until published.
- Knowledge drafts, archived items, expired items, wrong-channel items, and unpublished items are not injected into runtime.
- Knowledge document upload supports UTF-8 text and PDF. Upload parses into `draft_body`; publish creates `KnowledgeChunk` rows for the published version.
- Retrieval testing calls `/api/knowledge-items/retrieve-test` with the same metadata filters used by runtime.

## Runtime Boundary

WebChat Fast Reply still enters `provider_runtime.webchat_fast_dispatcher`. The dispatcher builds runtime metadata from the database:

- `persona_context`: active published `PersonaProfile` selected by market/channel/language priority.
- `knowledge_context`: keyword retrieval over current published chunks only.
- `safety_policy`: explicit boundary that knowledge is policy/SOP/FAQ only.
- `tracking_fact_metadata`: included only when trusted tracking evidence is present.

`CodexAppServerAdapter` forwards `persona_context`, `knowledge_context`, and `safety_policy` to the Codex appserver reply payload. The Node runtime validates these fields and compiles them into the compact strict-JSON prompt.

## Tracking Truth Boundary

Knowledge is never shipment evidence. Live parcel status requires:

- `tracking_fact_evidence_present=true`
- trusted `tracking_fact_summary`

The provider runtime output contract still rejects tracking status language when trusted evidence is absent.

## Rollback

- Persona rollback: `/api/persona-profiles/{id}/rollback` publishes a new version from a historical snapshot.
- Knowledge rollback: `/api/knowledge-items/{id}/rollback` publishes a new version and rebuilds chunks.
- Runtime rollback: archive the problematic knowledge item or disable the Persona, then publish/rollback the previous known-good version.
- Database rollback: downgrade Alembic revision `20260525_0033` to remove `knowledge_chunks` and the parsing/indexing columns.

## Validation

Focused gates for this surface:

```bash
pytest backend/tests/test_knowledge_items.py backend/tests/test_persona_profiles.py backend/tests/test_knowledge_runtime_context.py backend/tests/test_webchat_fast_reply_provider_runtime.py backend/tests/test_webchat_codex_app_server_provider.py backend/tests/test_provider_runtime_output_contracts.py
npm test
npm run build
cd tools/nexus-codex-runtime && npm test
python -m compileall backend/app
```
