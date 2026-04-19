# Multi-Tenant Foundation Report

## What this branch establishes

This branch upgrades Nexus Helpdesk from a single-tenant operational tool into a platform-oriented foundation for a hosted multi-tenant customer support product.

Implemented foundations:

- Tenant domain models (`tenants`, memberships, AI profile, tenant knowledge entries)
- Tenant-to-resource mappings for tickets, customers, teams, channel accounts, bulletins, AI config resources, and OpenClaw conversation links
- Tenant resolution via `X-Tenant-Id` or the user's default tenant membership
- Tenant management APIs for:
  - listing accessible tenants
  - creating a tenant
  - reading/updating current tenant AI profile
  - reading/creating/updating current tenant knowledge entries
- Tenant-aware ticket API wrappers (`/api/tickets`)
- Tenant-aware lite workspace wrappers (`/api/lite`)
- Minimal tenant control UI (`/tenant-control`)
- Alembic migration to create tenant tables and backfill a default tenant for existing data

## Tenant-scoped AI profile model

The tenant AI profile is intentionally structured rather than a single free-form prompt blob.

Current fields:

- `display_name`
- `brand_name`
- `role_prompt`
- `tone_style`
- `forbidden_claims`
- `escalation_policy`
- `signature_style`
- `language_policy`
- `system_prompt_overrides`
- `system_context`
- `enable_auto_reply`
- `enable_auto_summary`
- `enable_auto_classification`
- `allowed_actions`
- `default_model_key`

## Tenant knowledge layer

The knowledge layer is implemented as tenant-owned knowledge entries in the DB.

Current fields:

- `title`
- `category`
- `content`
- `source_type`
- `source_ref`
- `priority`
- `is_active`
- `tags_json`
- `metadata_json`

This is intentionally designed to evolve later into richer retrieval / RAG pipelines while already being usable as a structured tenant knowledge base.

## What is tenant-aware in this branch

### Backend

- Tenant selection and resolution dependency
- Ticket creation/listing/detail access in `/api/tickets`
- Lite workspace creation/listing/detail access in `/api/lite`
- Tenant AI profile APIs
- Tenant knowledge APIs

### Frontend

- A minimal tenant configuration entry point under `/tenant-control`
- Admin/manager-visible navigation entry in the shell

## OpenClaw integration status

This branch **does not modify OpenClaw core**.

Current tenant-aware position:

- Tenant context now exists in Nexus Helpdesk and can be resolved for a ticket
- Tenant-specific persona and knowledge are stored in first-class DB tables
- The next step is to wire every OpenClaw runtime path (especially auto-reply and deep sync/runtime hooks) to always resolve tenant context from ticket/conversation state before generating output

## Known limitations / next stage

This branch intentionally focuses on the multi-tenant foundation and minimal usable control plane.

Remaining next-stage items include:

1. Full tenant-aware OpenClaw runtime propagation across all bridge/background paths
2. More complete tenant-aware admin operations for markets, bulletins, channels, and AI config resources
3. Stronger tenant-aware customer deduplication and customer profile isolation rules
4. Package/plan/limit/billing controls for hosted SaaS scenarios
5. Richer UI for tenant memberships, tenant switching, and tenant admin roles

## Compatibility note

Some existing system capabilities remain compatibility-oriented and are not being marketed here as final optimal implementations.

Examples:

- Conversation lookup and attachment lookup in the OpenClaw bridge layer remain compatibility-oriented paths built on currently available Gateway capabilities
- This branch does not claim that all remaining runtime paths are already perfectly tenant-aware; it only claims the multi-tenant product foundation is now present in the repo and partially wired into the main operational flows
