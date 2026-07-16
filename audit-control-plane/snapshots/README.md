# Validated State Snapshots

Store immutable `VALIDATED_STATE_SNAPSHOT` artifacts here after ledger-truth publishes them.

Naming convention:

`YYYY/MM/DD/<snapshot-id>.json`

Rules:

- never overwrite a snapshot;
- bind it to one exact `main` SHA, one exact governance commit, and one protocol digest;
- `PARTIAL_CURRENT` may be retained as evidence but cannot authorize execution;
- use supersession or tombstone records instead of deletion.
