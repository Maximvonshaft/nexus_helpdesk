# Nexus Audit Control Plane Recovery Runbook

## Trigger conditions

Use this runbook when any of the following occurs:

- `main` is force-pushed or rewritten and loses the governance pointer or verifier;
- the pointer names a governance commit that no longer resolves;
- the protocol digest does not match;
- Issue #722, the main pointer, and the governance branch identify different current authorities;
- a snapshot or execution packet was issued against an invalid authority.

## Recovery principles

1. Do not force-move `governance/audit-control-plane`.
2. Do not reconstruct authority from branch names, PR titles, or the latest comment alone.
3. Start from the last exact governance commit independently recorded by both the main pointer or a trusted Issue #722 authority event.
4. Verify the protocol SHA-256 and append-only hash-chain continuity before restoring consumption.
5. Suspend all execution packets while authority is disputed.

## Main overwrite recovery

1. Resolve the last trusted governance commit.
2. Fetch `audit-control-plane/protocol/protocol-manifest.yaml` and the protocol at that commit.
3. Recompute the protocol SHA-256 and compare it with the trusted pointer.
4. Create a new integration branch from the current exact `main`; never reset `main` to the historical product tree.
5. Restore only:
   - `docs/governance/audit-control-plane.ref.json`;
   - `scripts/verify_governance_authority.py`;
   - the verifier tests.
6. Open a Draft PR, review the diff, and merge only through the accepted integration path.
7. Append one `RECOVERY_POINTER_UPDATE` event to Issue #722 containing exact old and new main SHAs, exact governance commit, protocol digest, PR, and verification evidence.
8. Ledger truth must invalidate or revalidate every snapshot, index, and execution packet created after the last trusted pointer.

## Governance branch damage recovery

1. Freeze consumption and mark all active packets `SUSPENDED`.
2. Create `governance/recovery-<timestamp>` from the last trusted exact governance commit.
3. Compare reachable governance history and identify the first missing, rewritten, or contradictory object.
4. Reconstruct only through new append-only commits; do not rewrite the damaged ref.
5. Independently review protocol, schemas, state pointers, hash chain, and recovery evidence.
6. Move authority to a new protected governance branch only through an explicit owner-approved protocol transition recorded in Issue #722 and `main`.

## Validation commands

The main verifier must fail closed when:

- the governance commit is absent or not 40 lowercase hexadecimal characters;
- the protocol path escapes its allowed root;
- the fetched protocol digest differs;
- the repository, branch, protocol ID, or protocol path differs from the pointer;
- a mutable branch-only URL is supplied without an exact commit;
- network or GitHub access fails.

## Safety

Recovery of governance authority does not authorize runtime code changes, release, deployment, provider activity, outbound activity, credentials, production-data mutation, or repository retirement.
