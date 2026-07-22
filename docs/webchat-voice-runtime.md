# Voice runtime

Voice sessions are Conversation-first and may remain ticketless. `ai_first` dispatches the configured LiveKit Agent into the same room. `human_first` creates a canonical Handoff and routes by tenant, country, channel, presence, heartbeat and independent voice capacity.

Agent decline returns only that offer to the queue. Ending an active call enters bounded wrap-up before ownership is released. Provider commands are idempotent and audited. Recording remains disabled by default and in controlled production.
