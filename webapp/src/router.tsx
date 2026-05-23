import { createRouter } from '@tanstack/react-router'
import { Route as RootRoute } from '@/routes/root'
import { Route as LoginRoute } from '@/routes/login'
import { Route as AdminRoute } from '@/routes/admin'
import { Route as IndexRoute } from '@/routes/index'
import { Route as WorkspaceRoute } from '@/routes/workspace'
import { Route as WebchatRoute } from '@/routes/webchat'
import { Route as WebchatVoiceRoute } from '@/routes/webchat-voice'
import { Route as WebCallRoute } from '@/routes/webcall'
import { Route as WebCallAIDemoRoute } from '@/routes/webcall-ai-demo'
import { Route as BulletinsRoute } from '@/routes/bulletins'
import { Route as AIControlRoute } from '@/routes/ai-control'
import { Route as ControlPlaneRoute } from '@/routes/control-plane'
import { Route as AccountsRoute } from '@/routes/accounts'
import { Route as UsersRoute } from '@/routes/users'
import { Route as RuntimeRoute } from '@/routes/runtime'

const routeTree = RootRoute.addChildren([
  LoginRoute,
  AdminRoute,
  IndexRoute,
  WorkspaceRoute,
  WebchatRoute,
  WebchatVoiceRoute,
  WebCallRoute,
  WebCallAIDemoRoute,
  BulletinsRoute,
  AIControlRoute,
  ControlPlaneRoute,
  AccountsRoute,
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
