# Nexus Audit Control Plane

This directory is the durable, file-backed authority for Nexus governance and audit coordination.

## Authority model

1. Consumers must resolve an **exact commit** on `governance/audit-control-plane`.
2. They must verify the protocol SHA-256 recorded by the current pointer.
3. `Issue #722` remains the append-only event stream and human entry point; comments are evidence, not implicit execution authority.
4. `main` carries only a replaceable pointer and verifier. It is not the sole storage location for governance authority.

## Safety boundary

This branch is not a product-development branch. It must never contain runtime implementation work, deployment changes, production credentials, provider configuration, customer data, or destructive repository operations.

Required repository controls:

- prohibit force-push and deletion;
- restrict direct writes;
- fast-forward updates only;
- never merge `main` into this branch;
- retain immutable history and explicit supersession records.

## Current protocol

- Protocol: `audit-control-plane/protocol/nexus-audit-controller-v3.1.yaml`
- Orchestration: `audit-control-plane/protocol/task-orchestration-v1.yaml`
- Manifest: `audit-control-plane/protocol/protocol-manifest.yaml`

## State products

- `VALIDATED_STATE_SNAPSHOT` — reconciled current facts at one exact `main` SHA.
- `NEXUS_EXECUTION_CONTEXT_INDEX` — active formal blocks, current findings, owners and packet references.
- `NEXUS_EXECUTION_PACKET` — the only bounded implementation handoff, issued by governance after all gates pass.

No PARTIAL or STALE result can authorize implementation, merge, deletion, release, deployment, provider activity, outbound activity, credentials, or production-data mutation.
