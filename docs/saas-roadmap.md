# SaaS Roadmap

## Do not add multi-tenant tables blindly

This patch does not force a risky tenant migration. The safe path is to design and rehearse organization isolation first.

## Phase 1: model design

- `organizations`
- `organization_users`
- `organization_id` on tickets/customers/attachments/channel accounts/integration clients/AI configs/audit logs
- tenant-scoped permissions

## Phase 2: compatibility migration

- create a default organization
- backfill all rows
- add non-null constraints after verification
- add compound indexes with `organization_id`

## Phase 3: commercial primitives

- plans
- quotas
- usage events
- channel count
- AI call count
- storage usage
- onboarding/offboarding runbooks
