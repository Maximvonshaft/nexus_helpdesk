# Dependency Map

## Internal dependencies

| Component | Depends on |
|---|---|
| Email capability registry | settings, ChannelAccount, EmailChannelAccount, ticket/customer email, suppression |
| Email send API | capability registry, schema validation, ticket visibility, send permission |
| Email adapter | EmailOutboundMetadata, EmailChannelAccount, provider abstraction |
| SES provider | email settings, secret resolution, boto3 or provider SDK |
| Delivery event webhook | provider verification, EmailDeliveryEvent, suppression |
| Inbound parser | email parser, ticket resolver, EmailInboundMessage |
| Frontend compose | capability API, send API schema |
| Admin account UI | ChannelAccount + EmailChannelAccount APIs |

## External dependencies

| External | Usage |
|---|---|
| AWS SES | Outbound transactional email |
| AWS SNS/EventBridge/S3 or bridge | Delivery events and inbound raw email |
| DNS/domain setup | SPF/DKIM/DMARC/verified identity |
| Secret manager or env | Provider credentials |
