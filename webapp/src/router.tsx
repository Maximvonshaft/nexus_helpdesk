import { createRouter } from '@tanstack/react-router'
import { NotFoundBoundary, Route as RootRoute } from '@/routes/root'
import { Route as LoginRoute } from '@/routes/login'
import { Route as IndexRoute } from '@/routes/index'
import { Route as WorkspaceRoute } from '@/routes/workspace'
import { Route as KnowledgeRoute } from '@/routes/knowledge'
import { Route as AgentControlRoute } from '@/routes/agent-control'
import { Route as ChannelsRoute } from '@/routes/channels'
import { Route as RuntimeRoute } from '@/routes/runtime'
import { Route as ControlTowerRoute } from '@/routes/control-tower'
import { Route as AdministrationRoute } from '@/routes/administration'
import { Route as AccountRoute } from '@/routes/account'
import { Route as WebchatRoute } from '@/routes/webchat'
import { Route as WebCallRoute } from '@/routes/webcall'

const routeTree = RootRoute.addChildren([
  LoginRoute,
  IndexRoute,
  WorkspaceRoute,
  KnowledgeRoute,
  AgentControlRoute,
  ChannelsRoute,
  RuntimeRoute,
  ControlTowerRoute,
  AdministrationRoute,
  AccountRoute,
  WebchatRoute,
  WebCallRoute,
])

export const router = createRouter({
  routeTree,
  notFoundMode: 'root',
  defaultNotFoundComponent: NotFoundBoundary,
  defaultPreload: 'intent',
  scrollRestoration: true,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
