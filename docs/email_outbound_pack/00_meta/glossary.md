# Glossary

| Term | Meaning |
|---|---|
| Outbound | Customer-facing reply initiated by an agent/system from NexusDesk. |
| Email Channel | Customer-support transactional email path attached to a ticket. |
| Capability Gate | Backend rule deciding whether a channel may be shown/sent. |
| ChannelAccount | Existing generic account registry row for a provider/channel. |
| EmailChannelAccount | New email-specific account configuration linked to ChannelAccount. |
| Provider | External email sending system, e.g. AWS SES. |
| Message-ID | RFC email message identifier used for threading. |
| In-Reply-To / References | Email headers used to link replies to original threads. |
| Bounce | Delivery failure event from recipient system/provider. |
| Complaint | Recipient spam complaint event. |
| Suppression | Blocklist preventing further email to risky recipients. |
| Return-Path | Address receiving bounces; provider may control this. |
| SES | Amazon Simple Email Service. |
