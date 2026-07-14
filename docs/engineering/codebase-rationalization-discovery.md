# Codebase Rationalization Discovery

## Authority

This discovery control implements the execution method in #744 while preserving the cross-cutting domain authority in #650 and `config/governance/legacy-surface-domains.v1.json`.

It does not authorize deletion. A finding fully owned by the legacy registry is routed to that domain. An unowned finding must be classified in `docs/ai/codebase-rationalization-inventory.v1.yaml` before the gate can pass.

## Signals

The scanner uses the tracked Git index and byte-bounded source reads to identify:

- non-standard root documents and root executables;
- filenames containing backup, copy, old, obsolete, deprecated, temporary, scratch, unused, duplicate or Round markers;
- exact duplicate non-empty tracked text files outside vendor and lockfile inputs;
- Webapp source modules unreachable from the Vite entry point through relative, alias, re-export and dynamic-import edges;
- backend application modules unreachable from the FastAPI entry point or tracked worker, script, Alembic and evaluation entry points.

These are investigation signals, not automatic deletion decisions. Dynamic registration, data compatibility, deployment consumers and historical contracts still require explicit evidence.

## Input hardening

- Only regular tracked files with Git index modes `100644` or `100755` are scanned; symlinks and gitlinks are excluded.
- Source reads fail closed above the configured byte limit or on binary/non-UTF-8 content.
- The execution ledger is byte-bounded and rejects duplicate YAML keys, anchors, aliases, merge keys and custom tags.

## Fail-closed behavior

The gate fails when:

- a finding has no legacy-domain owner and no execution-ledger classification;
- a finding is marked `DUPLICATE_DELETE`, `DEAD_DELETE` or `SUPERSEDED_DELETE` while the tracked asset still exists;
- a classification remains after its finding disappears;
- the registry or ledger is malformed.

`UNKNOWN_BLOCK_DELETE` may keep CI green only after it names an owner and next action. It remains visible and prevents #744 program completion.

## Verification surface

The permanent tests are named `test_agent_coordination_rationalization_discovery.py`, so the existing immutable Agent Coordination Self Test executes them without adding or changing a workflow. The repository integration test scans the exact checked-out head.

The result contains only repository paths, path fingerprints, finding IDs, owner Issue numbers and bounded counts. It does not include file contents, credentials, customer data, Provider payloads or unbounded logs.
