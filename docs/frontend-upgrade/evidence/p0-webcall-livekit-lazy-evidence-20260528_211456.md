# P0 WebCall LiveKit Lazy Loading Evidence

Time: 20260528_211456

## Why this PR exists

The deployed `main` proved the backend and SPA routes were healthy, but the browser could still feel stuck because WebCall media dependencies were part of the frontend graph too early.

The evidence before the fix showed top-level `livekit-client` imports in:

- `webapp/src/routes/webcall-ai.tsx`
- `webapp/src/routes/webcall.tsx`
- `webapp/src/components/webcall/AgentWebCallPanel.tsx`

## Source check after patch

Expected result: empty output.

```text

```

Dynamic imports after patch:

```text
webapp/src/routes/webcall-ai.tsx:130:      const { Room, RoomEvent, Track, createLocalAudioTrack } = await import('livekit-client')
webapp/src/routes/webcall.tsx:88:      const { Room, RoomEvent, Track, createLocalAudioTrack } = await import('livekit-client')
webapp/src/components/webcall/AgentWebCallPanel.tsx:228:      const { Room, RoomEvent, Track, createLocalAudioTrack } = await import('livekit-client')

```

## Build validation

```text

> helpdesk-suite-webapp@0.1.0 build
> tsc -b && vite build

vite v7.3.2 building client environment for production...
transforming...
✓ 295 modules transformed.
rendering chunks...
computing gzip size...
../frontend_dist/index.html                            0.84 kB │ gzip:   0.44 kB
../frontend_dist/assets/index-B1kx6jVf.css            49.47 kB │ gzip:  12.09 kB
../frontend_dist/assets/vendor-D8rjULs-.js            35.46 kB │ gzip:  13.23 kB
../frontend_dist/assets/vendor-radix-DnOYB7xw.js      59.67 kB │ gzip:  17.63 kB
../frontend_dist/assets/vendor-tanstack-DusGkT5O.js  121.00 kB │ gzip:  37.85 kB
../frontend_dist/assets/vendor-react-eJeque6w.js     144.19 kB │ gzip:  46.53 kB
../frontend_dist/assets/index-DGHRv3f-.js            311.48 kB │ gzip:  86.00 kB
../frontend_dist/assets/vendor-livekit-CU0U2kIO.js   519.55 kB │ gzip: 136.37 kB
✓ built in 2.64s

```

## Index eager-load check

```text
PASS: index.html has no eager livekit reference

```

## JS asset sizes after patch

```text
index-DGHRv3f-.js 311480 bytes
vendor-D8rjULs-.js 35458 bytes
vendor-livekit-CU0U2kIO.js 519549 bytes
vendor-radix-DnOYB7xw.js 59672 bytes
vendor-react-eJeque6w.js 144188 bytes
vendor-tanstack-DusGkT5O.js 121001 bytes

```

Async LiveKit chunks after patch:

```text
vendor-livekit-CU0U2kIO.js 519549 bytes

```

## Test validation

```text
TAP version 13
# Subtest: email workbench route uses unified routeAccess RBAC semantics
ok 1 - email workbench route uses unified routeAccess RBAC semantics
  ---
  duration_ms: 0.722045
  type: 'test'
  ...
# Subtest: email workbench is reachable from AppShell navigation and command palette
ok 2 - email workbench is reachable from AppShell navigation and command palette
  ---
  duration_ms: 0.24794
  type: 'test'
  ...
# Subtest: email queue filter uses tokenized channel markers instead of loose substring regex
ok 3 - email queue filter uses tokenized channel markers instead of loose substring regex
  ---
  duration_ms: 0.181819
  type: 'test'
  ...
# Subtest: email workbench closes draft save, outbound send, and timeline refresh loops
ok 4 - email workbench closes draft save, outbound send, and timeline refresh loops
  ---
  duration_ms: 0.191459
  type: 'test'
  ...
# Subtest: provider credentials nav route is registered in router
ok 5 - provider credentials nav route is registered in router
  ---
  duration_ms: 0.846536
  type: 'test'
  ...
# Subtest: outbound email admin nav route is registered and capability gated
ok 6 - outbound email admin nav route is registered and capability gated
  ---
  duration_ms: 0.127739
  type: 'test'
  ...
# Subtest: email operator workbench nav route is registered and capability gated
ok 7 - email operator workbench nav route is registered and capability gated
  ---
  duration_ms: 1.157593
  type: 'test'
  ...
# Subtest: internal webcall routes are intentionally classified
ok 8 - internal webcall routes are intentionally classified
  ---
  duration_ms: 0.232808
  type: 'test'
  ...
# Subtest: primary nav internal hrefs have matching registered routes
ok 9 - primary nav internal hrefs have matching registered routes
  ---
  duration_ms: 2.817415
  type: 'test'
  ...
# Subtest: livekit-client is not imported at module top-level by WebCall entrypoints
ok 10 - livekit-client is not imported at module top-level by WebCall entrypoints
  ---
  duration_ms: 0.916325
  type: 'test'
  ...
# Subtest: livekit media primitives remain inside explicit media action paths
ok 11 - livekit media primitives remain inside explicit media action paths
  ---
  duration_ms: 1.200384
  type: 'test'
  ...
# Subtest: top-level /webcall operator route is registered without replacing customer room route
ok 12 - top-level /webcall operator route is registered without replacing customer room route
  ---
  duration_ms: 0.777676
  type: 'test'
  ...
# Subtest: webcall operator entry is routeAccess gated and visible in operator navigation
ok 13 - webcall operator entry is routeAccess gated and visible in operator navigation
  ---
  duration_ms: 0.254989
  type: 'test'
  ...
# Subtest: webcall workbench uses real backend contracts for queue, identity, AI, handoff, and audit
ok 14 - webcall workbench uses real backend contracts for queue, identity, AI, handoff, and audit
  ---
  duration_ms: 0.357118
  type: 'test'
  ...
1..14
# tests 14
# suites 0
# pass 14
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 141.128514

```

## Evidence folder

`/root/nexus_p0_dedupe_livekit_20260528_211456`
