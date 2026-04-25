# Round B Operator Demo Script

## Demo story

A customer is on a merchant website and asks: “Where is my parcel?” NexusDesk automatically opens a Webchat ticket. The support agent sees the Webchat source, reviews the conversation, sends a safe reply, and the customer sees the reply in the website widget.

## Demo steps

1. Open the demo website:

```text
/webchat/demo.html
```

2. Click “Chat with us”.

3. Send:

```text
Where is my parcel?
```

4. Open NexusDesk admin UI.

5. Go to:

```text
/webchat
```

6. Select the newest Webchat conversation.

7. Show these fields:

- Ticket number
- Visitor name/contact when available
- Website origin
- Page URL
- Message timeline

8. Try unsafe reply:

```text
SECRET_KEY leaked in stack trace token password
```

Expected result: blocked by safety gate.

9. Send safe reply:

```text
We have received your request and will check it shortly.
```

10. Return to visitor widget and wait for polling refresh.

Expected result: customer sees agent reply.

## Demo talking points

- “This is no longer just WhatsApp or email. Any customer website can become an intake channel.”
- “Every visitor message becomes a trackable NexusDesk ticket.”
- “Support agents work in one queue, not in scattered browser tabs.”
- “Outbound replies are safety-gated before the customer sees them.”
- “Round C can let OpenClaw draft suggested replies, but human approval remains the control point.”
