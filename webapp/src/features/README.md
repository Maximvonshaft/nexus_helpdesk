# features layer

The `features` layer owns the current user-facing workflows:

- `operator-workspace`;
- `knowledge`;
- `channels`;
- `runtime`;
- `control-tower`.

Each domain has one implementation and one lazy boundary. Generic controls come directly from MUI; bounded operational states come from `app/OperatorPresentation.tsx`; HTTP calls delegate to the canonical `lib/apiClient.ts` transport through domain adapters.

A feature must not import another feature's private internals, create a second route/store/API authority, add route CSS, or create generic Button/Input/Dialog wrappers.
