# Nexus WebCall Canary Readiness

## Purpose

This runbook defines the non-production readiness gate for the canonical LiveKit WebCall path. It does not enable a carrier, DID, SIP trunk, recording, transcription, AI voice, or outbound calling.

## Canonical surfaces

- Public and operator media page: `/webcall/{voice_session_id}`.
- Incoming operator offer: existing authenticated AppShell control.
- Conversation context: canonical Workspace and Conversation timeline.
- Provider control: durable Voice Commands and Telephony Event Inbox.
- Acceptance authority: the single Canonical Acceptance workflow on the exact PR Head.

There is no fallback Voice page, second operator console, legacy media route, or compatibility redirect.

## Safety boundary

The canary must not:

- deploy or modify production;
- change real LiveKit, carrier, DID, trunk, dispatch-rule, or model credentials;
- request microphone permission before an explicit join or accept action;
- expose room names, participant identities, Provider references, API credentials, or participant tokens in the incoming-offer UI;
- infer production authorization from green tests.

## Required repository gate

The exact candidate Head must pass:

1. Static service-authority and telephony-residue qualification.
2. Complete backend regression.
3. PostgreSQL migration and acceptance rehearsal.
4. Frontend architecture, lint, typecheck, units, build, and route splitting.
5. Playwright browser journeys.
6. Image build, migration, startup, readiness, Trivy, and SBOM.
7. Secret scanning, SAST, dependency audit, and CodeQL.
8. The final required gate.

## Controlled two-browser proof

Run only against an explicitly isolated non-production environment with test credentials.

1. Browser A starts a Voice session and opens `/webcall/{voice_session_id}`.
2. Browser A explicitly joins and grants microphone permission.
3. Browser B receives one capability- and scope-filtered incoming offer in the authenticated AppShell.
4. Browser B explicitly accepts; the same Conversation and Handoff become authoritative.
5. Confirm two-way audio in the same LiveKit Room.
6. Exercise hold/resume and DTMF through durable Provider-confirmed commands.
7. Exercise cold transfer and warm consultation start/complete/cancel without creating another Room or Conversation.
8. End the call and confirm one canonical terminal timeline outcome.
9. Complete required wrap-up before capacity is released.

## Evidence requirements

The canary evidence must prove:

- no microphone access before explicit user action;
- no Provider credentials or topology in public or incoming-offer responses;
- one Conversation, one Handoff, one Voice Session, one Room, and one owner;
- offer decline or timeout does not terminate the customer call;
- warm consultation start is not reported as transfer completion;
- Provider failures remain visibly unconfirmed;
- customer-visible terminal failures are localized and retryable;
- no second transcript, AI action, queue, or compatibility route exists.

Any failed gate or missing Provider evidence blocks release promotion.
