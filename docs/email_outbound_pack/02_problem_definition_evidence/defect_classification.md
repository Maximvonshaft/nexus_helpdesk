# Defect Classification

## Current defect type

Email is not a bug in the sense of a broken implementation; it is a deliberate production gap.

Classification:
- Category: Incomplete channel implementation.
- Severity if exposed prematurely: S0/S1.
- Current severity while blocked: S3 operational gap.
- Target priority: P0 for production support rollout.

## Failure modes if implemented poorly

1. Email visible but not sendable.
2. Email sent to wrong recipient.
3. Duplicate sends on worker retry.
4. Provider accepted send but Nexus marks failure and retries.
5. Bounce/complaint ignored.
6. Customer replies lost outside NexusDesk.
7. HTML/script injection in timeline.
8. Secrets leaked in logs or DB.
9. Attachments sent without visibility rules.
10. Open/click tracking enabled without privacy review.
