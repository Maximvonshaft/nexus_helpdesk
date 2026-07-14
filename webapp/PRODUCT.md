# Nexus Customer Service Frontend Product Register

## Product identity

Nexus is a **customer-case resolution console for logistics customer service**.

The authenticated frontend is not a chatbot dashboard, engineering console, model console, generic admin portal, or collection of disconnected tools. It is one work surface that helps a customer-service operator understand the customer, verify facts, take responsibility, complete the next permitted action, communicate clearly, and follow the case through to a confirmed result.

## Primary job

For every case, the interface must make seven answers obvious:

1. Who is the customer and what do they need?
2. Which facts are verified and which details are still missing?
3. Who owns the case and when is it due?
4. What is the next permitted action?
5. What actually happened after the action was submitted?
6. Has the customer received a clear update?
7. Can the case be completed, observed, repaired, or reopened?

The canonical journey is:

`Login → Scoped customer queue → Case → Verified facts → Ownership → Service action → Operational result → Customer reply → Completion or follow-up`

## Users

### Customer-service operator

- Works from a scoped queue.
- Reads the customer request before internal records.
- Distinguishes customer claims from verified operational facts.
- Performs only permitted actions.
- Communicates in clear customer language.
- Does not treat a submitted request or technical status as a solved customer problem.

### Team lead

- Monitors unowned work, overdue work, escalations, and repair-required cases.
- Takes over, assigns, releases, or reroutes work through controlled actions.
- Reviews why cases cannot be completed.

### Knowledge steward

- Maintains approved customer-service facts, policies, and procedures.
- Reviews scope, wording, and publication status.
- Cannot override live operational facts or action authority.

### Channel administrator

- Confirms customer contact channels are active and healthy.
- Resolves connection issues without receiving unrelated case access.

### System administrator

- Reviews bounded service availability and raises technical incidents.
- Internal diagnostics are not part of the ordinary customer-service workflow.

## Supported routes

| Route | Customer-service job |
|---|---|
| `/login` | Establish operator identity |
| `/workspace` | Process customer cases from request to result |
| `/knowledge` | Maintain customer-service facts, policies, and procedures |
| `/channels` | Confirm customer contact channels are available |
| `/system` | Confirm supporting services are available and escalate faults |

`/webchat` is compatibility-only and redirects to `/workspace`. It never mounts a second console.

## Information hierarchy

Every case screen uses this order:

1. Customer identity, request, urgency, ownership, and due time.
2. The most important next action.
3. Verified facts, customer claims, missing information, and conflicts.
4. Customer conversation and reply composer.
5. Permitted service and operational actions.
6. Actual action and notification results.
7. Completion blocker, follow-up, or reopen state.

Raw identifiers, transport states, internal service names, and implementation traces are not primary customer-service content.

## Product language

Use customer-service language:

- Customer request
- Verified fact
- Customer statement
- Missing information
- Assigned / unassigned
- Due soon / overdue
- Next action
- Request accepted
- Operational result confirmed
- Customer notified
- Needs repair
- Follow-up required
- Ready to complete
- Reopened

Do not expose internal automation, model, provider, prompt, inference, or runtime terminology to customer-service operators.

## Behavior principles

- One primary action for the current case state.
- Backend permissions remain final.
- Empty states explain the next valid step.
- Disabled actions explain why they are unavailable.
- A submitted request is not displayed as a solved problem.
- Customer notification is distinct from operational completion.
- Unsaved replies and knowledge edits are protected before navigation.
- Refresh preserves durable state and does not duplicate commands.
- Keyboard, screen-reader, touch, responsive, slow-network, and large-list behavior are product requirements.

## Frontend authority

- Production source: `webapp/` only.
- Route spine: `/workspace` only for operator case work.
- Tokens: `webapp/src/styles/tokens.css`.
- Shared components: `webapp/src/components/ui/`.
- Feature styles may consume semantic tokens but may not create a second palette or component vocabulary.
- Legacy `frontend/`, Support Console, and `shared/ui` authorities are prohibited.