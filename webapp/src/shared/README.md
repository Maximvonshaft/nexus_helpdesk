# shared layer

The `shared` layer owns reusable frontend infrastructure and generic UI.

Target shared domains:

- api
- auth
- ui
- layout
- realtime
- hooks
- schemas
- telemetry
- utils

This foundation branch does not move existing modules yet. Future moves must preserve behavior and include targeted smoke evidence.

Allowed dependencies:

- external libraries
- local shared submodules when necessary

Not allowed:

- importing from `features`
- importing from `entities`
- owning business workflow logic
