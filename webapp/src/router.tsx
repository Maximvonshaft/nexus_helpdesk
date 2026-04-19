import { createRouter } from '@tanstack/react-router'
import { Route as RootRoute } from '@/routes/root'
import { Route as LoginRoute } from '@/routes/login'
import { Route as IndexRoute } from '@/routes/index'
import { Route as WorkspaceRoute } from '@/routes/workspace'
import { Route as BulletinsRoute } from '@/routes/bulletins'
import { Route as AIControlRoute } from '@/routes/ai-control'
import { Route as AccountsRoute } from '@/routes/accounts'
import { Route as RuntimeRoute } from '@/routes/runtime'
import { Route as TenantControlRoute } from '@/routes/tenant-control'

const routeTree = RootRoute.addChildren([
  LoginRoute,
  IndexRoute,
  WorkspaceRoute,
  BulletinsRoute,
  AIControlRoute,
  TenantControlRoute,
  AccountsRoute,
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
