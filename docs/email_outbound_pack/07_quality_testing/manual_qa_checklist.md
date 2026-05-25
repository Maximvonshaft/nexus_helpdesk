# Manual QA Checklist

## Agent UI

- [ ] Email appears only when ready.
- [ ] Disabled Email shows missing reason.
- [ ] To/From/Subject are visible.
- [ ] Send button is disabled until required fields are valid.
- [ ] Error messages are actionable.

## Backend

- [ ] Email queued row created.
- [ ] Metadata row created.
- [ ] Worker claims and sends.
- [ ] Provider id stored.
- [ ] Ticket timeline updates.

## Delivery

- [ ] Delivery event stored.
- [ ] Bounce event stored.
- [ ] Complaint event stored.
- [ ] Suppression blocks future sends.

## Inbound

- [ ] Customer reply links to ticket.
- [ ] Unknown inbound email is safely unresolved or creates configured ticket path.
