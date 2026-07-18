# Local ExternalChannel readiness report

> Historical evidence only. ExternalChannel transport and bridge execution are retired/disabled and this document must not be used as a bring-up runbook.

The historical change set introduced local connectivity checks, environment templates, a compose topology, bootstrap helpers and smoke verification for an ExternalChannel bridge. Those assets are not current runtime authority.

Current policy is fail-closed:

- `EXTERNAL_CHANNEL_TRANSPORT=disabled`
- `EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled`
- no bridge, daemon, CLI fallback, or second customer-message transport may be activated

Persisted ExternalChannel names may remain only where required to read historical data or maintain bounded schema compatibility.
