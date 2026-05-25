# Support Playbook

## Common scenarios

### Customer email missing

Action:
- Ask agent to add or confirm email.
- Do not use a random email from notes unless verified.

### Sender account not verified

Action:
- DevOps/admin must complete provider/domain verification.

### Email stuck pending

Action:
- Check worker.
- Check provider credentials.
- Check queue lock.
- Check dead-letter state.

### Email bounced

Action:
- Verify email address.
- Update customer profile.
- Do not repeatedly resend to same address.

### Customer says they did not receive email

Action:
- Check delivery event.
- Confirm recipient.
- Ask customer to check spam/junk.
- Use alternative channel if urgent.
