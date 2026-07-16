# Nexus Execution Packets

This directory stores immutable subject-specific implementation handoffs issued by the governance lane.

No packet may exist unless its source snapshot and context index are both `FINAL_CURRENT`, the subject is `GOVERNANCE_ACCEPTED`, and all ownership, dependency, rollback, verification, expiry, and prohibited-effect fields are complete.

A packet is not merge, release, deployment, provider, outbound, credential, or production-data authorization unless those exact effects are separately and explicitly authorized by the repository owner.
