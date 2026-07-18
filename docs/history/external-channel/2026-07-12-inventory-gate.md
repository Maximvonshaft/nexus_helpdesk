# ExternalChannel inventory gate — historical record

On 2026-07-12 the repository introduced a manifest/checker/GitHub Actions design intended to classify every ExternalChannel reference and prevent reintroduction.

That design is no longer current authority. The workflow was later retired, the inventory drifted from the repository, and maintaining it would recreate a second governance control plane. Current authority is documented in `docs/engineering/external-channel-decommission.md`, `config/architecture/compatibility-lifecycle.v1.json`, and the local repository verifier.

This record preserves the rationale only. It must not be used to restore the deleted inventory, checker, tests, workflow or runtime behavior.
