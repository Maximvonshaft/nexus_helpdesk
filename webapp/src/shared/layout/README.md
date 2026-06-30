# shared/layout

Target home for reusable layout primitives such as AppShell subparts, panels, page headers, split panes, and event docks.

`OperationsShell.tsx` is the first active primitive in this layer. It owns the stable operations cockpit zones only: sidebar, topbar, main workspace, right context panel, and bottom event dock. Route-specific business rendering stays in existing route modules.
