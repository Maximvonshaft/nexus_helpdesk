# Go / No-go Checklist

## Go only if all are true

- [ ] Migration applied successfully.
- [ ] Email disabled by default after deploy.
- [ ] Staging smoke send passed.
- [ ] Provider id persisted.
- [ ] Delivery event captured.
- [ ] Bounce/complaint captured.
- [ ] Inbound reply linked.
- [ ] Existing outbound regression tests passed.
- [ ] Rollback command tested.
- [ ] Security review completed.
- [ ] Support lead understands operator SOP.

## No-go if any are true

- [ ] Email sends without capability ready.
- [ ] Duplicate send observed.
- [ ] Wrong recipient possible.
- [ ] Webhook unauthenticated.
- [ ] Secrets appear in logs/DB.
- [ ] Bounce/complaint ignored.
- [ ] Existing WhatsApp/WebChat breaks.
