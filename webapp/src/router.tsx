import { createRouter } from '@tanstack/react-router'
import { NotFoundBoundary, Route as RootRoute } from '@/routes/root'
import { Route as LoginRoute } from '@/routes/login'
import { Route as IndexRoute } from '@/routes/index'
import { Route as WorkspaceRoute } from '@/routes/workspace'
import { Route as WebchatRoute } from '@/routes/webchat'

const routeTree = RootRoute.addChildren([
  LoginRoute,
  IndexRoute,
  WorkspaceRoute,
  WebchatRoute,
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
