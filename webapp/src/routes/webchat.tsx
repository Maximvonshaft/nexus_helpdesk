import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat',
  beforeLoad: () => {
    throw redirect({ to: getSupportToken() ? '/workspace' : '/login', replace: true })
  },
})
