import { createRouter } from '@tanstack/react-router'
import { Route as RootRoute } from '@/routes/root'
import { Route as LoginRoute } from '@/routes/login'
import { Route as AdminRoute } from '@/routes/admin'
import { Route as IndexRoute } from '@/routes/index'
import { Route as WorkspaceRoute } from '@/routes/workspace'
import { Route as WebchatRoute } from '@/routes/webchat'
import { Route as EmailRoute } from '@/routes/email'
import { Route as FastLaneRoute } from '@/routes/fast-lane'
import { Route as WebchatVoiceRoute } from '@/routes/webchat-voice'
import { Route as WebCallOperatorRoute } from '@/routes/webcall-operator'
import { Route as WebCallRoute } from '@/routes/webcall'
import { Route as WebCallAIProductionRoute } from '@/routes/webcall-ai'
import { Route as WebCallAIDemoRoute } from '@/routes/webcall-ai-demo'
import { Route as ProviderCredentialsRoute } from '@/routes/provider-credentials'
import { Route as BulletinsRoute } from '@/routes/bulletins'
import { Route as AIControlRoute } from '@/routes/ai-control'
import { Route as ControlPlaneRoute } from '@/routes/control-plane'
import { Route as AccountsRoute } from '@/routes/accounts'
import { Route as OutboundEmailRoute } from '@/routes/outbound-email'
import { Route as UsersRoute } from '@/routes/users'
import { Route as RuntimeRoute } from '@/routes/runtime'

const routeTree = RootRoute.addChildren([
  LoginRoute,
  AdminRoute,
  IndexRoute,
  WorkspaceRoute,
  WebchatRoute,
  EmailRoute,
  FastLaneRoute,
  // Internal operator console for human WebCall handling; retained as a legacy deep link.
  WebchatVoiceRoute,
  // Top-level operator WebCall workbench with voice, handoff, customer profile, AI suggestion, and audit context.
  WebCallOperatorRoute,
  // Public/customer WebCall room; linked from widget runtime with visitor token context.
  WebCallRoute,
  WebCallAIProductionRoute,
  // Internal ops-only AI sandbox; AppShell exposes it only to ops-capable users.
  WebCallAIDemoRoute,
  ProviderCredentialsRoute,
  BulletinsRoute,
  AIControlRoute,
  ControlPlaneRoute,
  AccountsRoute,
  OutboundEmailRoute,
  UsersRoute,
  RuntimeRoute,
])

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
  scrollRestoration: true,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
