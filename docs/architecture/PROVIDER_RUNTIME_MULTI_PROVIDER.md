# Multi-Provider Runtime Architecture

NexusDesk has transitioned its WebChat Fast Lane to a Multi-Provider Runtime.
The backend acts as the control plane. All external AI models (OpenAI, Codex, Anthropic, ExternalChannel) act as candidate "generators".
The final output is aggressively parsed and verified by NexusDesk `StrictOutputParser` and `OutputContracts`.

## Flow
1. Inbound WebChat -> `generate_webchat_fast_reply`
2. Routes to `ProviderRuntimeRouter`.
3. `ProviderRuntimeRouter` reads `provider_routing_rules` (Primary + Fallbacks).
4. Calls `ProviderAdapter.generate(request)`.
5. The result must be JSON matching the `output_contract` (e.g. `speedaf_webchat_fast_reply_v1`).
6. If the JSON is invalid, malformed, or missing required fields, the router fails-closed and triggers the fallback adapter.
7. Final output is emitted back to the Fast Lane.

## Output Contracts
Provider adapters do not produce markdown. They produce structured dictionaries governed by schemas in `output_contracts.py`.
