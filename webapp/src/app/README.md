# app layer

The `app` layer owns the single Nexus application frame.

Current authorities:

- `main.tsx` mounts one query client, router and `NexusThemeProvider`;
- `AppShell.tsx` owns the authenticated shell, account and work-scope controls;
- `navigation.ts` owns capability-derived navigation;
- `OperatorPresentation.tsx` owns bounded operational empty, error, loading, fact-grid and tone presentation.

The layer must not own feature business logic, backend transport implementations, a second theme, or route-private visual systems.
