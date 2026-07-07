import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: getSupportToken() ? '/webchat' : '/login' })
  },
})
