# Nexus OSR Frontend Semantic Foundation and Login — Design Specification

**Work Item:** #621
**Frontend audit:** #611
**Product/design authority:** #613 / PR #615
**Baseline:** `main@6f943319a934ee60c68c20bd26c7cb118b1e45d2`

## Purpose

Apply the merged Nexus OSR product/design authority to the shared React primitives and the current Login route. This slice establishes an executable semantic component baseline before #525 builds the canonical Workspace.

It does not implement `/workspace`, Case Spine runtime state, queue behavior, business closure, Provider actions or backend contracts.

## Applied skill chain

### Anthropic frontend-design

- Ground the interface in logistics operations rather than a generic SaaS login.
- Use one justified signature element and keep the rest restrained.
- Make structure encode the true operating model.
- Write from the operator's side using plain, active language.
- Remove gradient/glass/card-template defaults.

### UI/UX Pro Max

- Accessibility and touch quality precede decoration.
- Normal text contrast ≥4.5:1.
- Primary/touch targets ≥44×44 CSS pixels.
- Visible labels, focus, loading/error feedback and semantic controls.
- Structural responsive behavior at 375 / 768 / 1024 / 1440.
- Motion 150–300ms, meaningful and reduced-motion safe.
- React implementation avoids unnecessary new dependencies and preserves current request/cache behavior.

### Impeccable

- Read PRODUCT/DESIGN and align before polishing.
- Use the Product register: earned familiarity, restrained color, one family and consistent affordances.
- Shape before craft; extract reusable patterns incrementally.
- Cover eight interactive states where applicable.
- Harden for errors, long translations, mobile, slow network and keyboard use.
- Browser evidence is required; a clean build is not visual acceptance.

## Confirmed task brief

- **User:** support and operations staff beginning a focused shift.
- **Primary action:** authenticate and enter the governed operator environment.
- **State of mind:** focused, time-sensitive and trust-oriented.
- **Fidelity:** production-ready.
- **Breadth:** Login and shared primitives only.
- **Theme:** restrained light workspace with one dark operational context panel.
- **Anti-goals:** generic gradient, glass card, over-rounding, fake metrics, runtime jargon and false business claims.

## Visual direction

### Scene sentence

A logistics operator signs in at a desktop workstation under ordinary office light before taking responsibility for active cases; the interface must feel calm, exact and operational rather than promotional.

### Composition

Desktop:

```text
┌──────────────── operational context ────────────────┬──────── sign-in task ────────┐
│ Nexus OSR                                            │ Enter operations workspace    │
│ Facts → Governed action → Closure                    │ Account                       │
│ short product principles                             │ Password [show]                │
│                                                      │ [Sign in to workspace]         │
└──────────────────────────────────────────────────────┴───────────────────────────────┘
```

Mobile:

```text
┌──────── compact context ────────┐
│ Nexus OSR + three truthful steps │
├──────────────────────────────────┤
│ Sign-in form                     │
│ full-width 44px controls         │
└──────────────────────────────────┘
```

### Signature

A compact three-stage orientation sequence:

`Fact → Governed action → Closure`

It is explanatory product context, not runtime status. It must not suggest that a real case has progressed.

## Shared semantic component contract

### Button

- Semantic classes are authoritative: `nd-button`, size and variant classes.
- Compatibility `.button` may remain temporarily for existing feature selectors.
- Props include size and loading state.
- Loading disables duplicate submission and sets `aria-busy`.
- Applicable states: default, hover, focus-visible, active, disabled, loading.
- Medium/default target is at least 44px.

### Badge

- Uses semantic tone classes.
- Tone supports meaning but visible text remains the primary label.
- No raw component colors.

### Field and controls

- Use a group container plus explicit `<label htmlFor>`.
- Preserve `aria-describedby`, `aria-invalid`, required and caller IDs/classes.
- Inputs, selects and textareas use one semantic control vocabulary.
- Error text is linked to the control and announced.

### Page header

- Supports a semantic heading level.
- Login uses `h1`.
- Eyebrow is optional and must encode real context rather than decorate every section.

## Login behavior

- Root landmark is `<main>`.
- Authentication fields live in a semantic `<form>`.
- Enter submits from either input.
- Account and password have visible labels and autocomplete attributes.
- Password visibility is an accessible text control with `aria-pressed` and a 44px target.
- Submission uses one primary action: `登录运营工作台`.
- Loading copy: `正在验证账号…`.
- Error copy is bounded and actionable: `无法登录。请检查账号和密码后重试。`
- Error uses `role="alert"`, can receive focus and does not expose raw response payloads.
- Current Auth API, session token and `/webchat` redirect remain unchanged.

## Tokens and CSS authority

- `tokens.css` defines semantic color, spacing, type, motion, focus, control and elevation values.
- `components.css` defines shared primitives and is imported after legacy compatibility CSS so semantic classes win.
- Feature styles may arrange primitives but do not define another palette or control system.
- Login-specific CSS consumes semantic tokens.
- No decorative gradient, translucent glass card or card radius above 12px.

## Accessibility and responsive requirements

- 4.5:1 normal text contrast.
- Focus indicator ≥3:1 against adjacent colors.
- 44px primary/touch target.
- No hover-only behavior.
- 16px mobile inputs.
- No horizontal page scroll at 375px.
- Text enlargement does not hide controls.
- Reduced motion removes non-essential transitions.
- Password state and login errors are announced semantically.

## Performance and hardening

- Add no dependency or icon package.
- Use text for password visibility.
- Preserve current React Query Auth mutation.
- Prevent double submission.
- Preserve input on error.
- Support long translated labels with wrapping rather than fixed widths.
- No new polling, image or font network request.

## Delivery boundary

Allowed changes are shared frontend primitives, tokens, Login presentation/behavior, focused tests and a dedicated read-only CI gate.

No backend API, database, migration, queue, Ticket, Case, Provider, outbound, deployment or production-data behavior changes.