# Outbound Safety Architecture

## Current patch

This patch introduces a deterministic `outbound_safety.py` gate. It blocks empty/sensitive messages and routes logistics factual claims or AI-generated replies to human review unless explicit fact evidence is available.

## Target architecture

1. Meta Gate: blocks identity probing, system prompt leakage, and unsupported intent.
2. Fact Gate: requires tool evidence for parcel status, SLA, customs, compensation, delivery promises.
3. Truth Gate: reviews generated text for unsupported claims.
4. Unified Outbound Dispatcher: single egress path for WhatsApp/email/web chat.
5. Tool Permission Boundary: OpenClaw tools scoped by tenant/channel/account/peer.

## Rule

Channel adapters must not send directly. They must call the dispatcher, and the dispatcher must call the safety gates before any external send.
