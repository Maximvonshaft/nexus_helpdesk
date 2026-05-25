# Chatwoot Sidecar for NexusDesk

This repository vendors Chatwoot as an isolated Git submodule under `vendor/chatwoot`.

Pinned upstream:

- Upstream repository: `https://github.com/chatwoot/chatwoot.git`
- Pinned tag/release commit: `v4.14.0` / `81cb75b62feaaea25d8d4baaf099c46a8eb65c15`
- License baseline: Chatwoot Community Edition is MIT; Enterprise code/features must be treated separately and must not be assumed free.

## Why this is isolated

Chatwoot is useful for NexusDesk as a reference or optional sidecar for:

- WebChat widget behavior
- Omnichannel inbox UX
- Conversation/contact/message modeling
- Agent workspace interaction patterns
- API channel and webhook integration patterns
- Help center and saved reply workflows

It is not the NexusDesk core business runtime. NexusDesk should keep logistics objects and AI/agent orchestration in its own domain model, including parcels, tracking snapshots, delivery attempts, POD evidence, exception cases, SLA policy, AI tool calls, audit logs, Speedaf integrations, OpenClaw/MCP bridges, and WebCall runtime.

## Local checkout

From the repository root:

```bash
git submodule update --init --recursive vendor/chatwoot
```

If you clone this repository fresh, use:

```bash
git clone --recurse-submodules git@github.com:Maximvonshaft/nexus_helpdesk.git
```

## Update policy

Do not casually move the submodule pointer. Any Chatwoot upgrade should be done in a dedicated PR with:

1. old Chatwoot commit and new Chatwoot commit;
2. upstream release notes reviewed;
3. license/enterprise boundary reviewed;
4. Nexus integration notes updated;
5. security-impact notes added;
6. rollback command documented.

Recommended update flow:

```bash
cd vendor/chatwoot
git fetch --tags origin
git checkout <approved-chatwoot-tag-or-commit>
cd ../..
git add vendor/chatwoot .gitmodules docs/vendor/CHATWOOT_SIDECAR.md
git commit -m "chore(vendor): update chatwoot sidecar pointer"
```

## Architecture rule

Preferred pattern:

```text
NexusDesk Core owns logistics workflow and AI agent decisions.
Chatwoot is only a sidecar/reference/channel-inbox candidate.
```

Do not embed Nexus logistics domain rules directly into Chatwoot until a formal integration RFC is approved.

## First integration candidates

1. Read-only UX audit: compare Chatwoot inbox/conversation UI with Nexus agent console.
2. Webhook bridge PoC: map Chatwoot conversation events into Nexus `Conversation`, `Message`, and `Ticket Event` records.
3. API-channel PoC: create a Nexus-owned channel adapter that can ingest/send messages while keeping Nexus as the source of truth.
4. Agent-assist PoC: use Nexus AI/OpenClaw runtime to draft replies, not Chatwoot Enterprise AI as a hard dependency.

## Safety boundary

- Do not copy Enterprise-only code or behavior into Nexus.
- Do not commit credentials, Chatwoot secrets, SMTP credentials, WhatsApp tokens, or webhook signing secrets.
- Do not deploy Chatwoot as production dependency until data retention, PII, tenancy, audit logging, and outbound-message ownership are reviewed.
