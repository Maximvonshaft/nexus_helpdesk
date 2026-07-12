import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/',
  beforeLoad: () => {
    if (getSupportToken()) throw redirect({ to: '/workspace' })
    throw redirect({ to: '/login' })
  },
})
