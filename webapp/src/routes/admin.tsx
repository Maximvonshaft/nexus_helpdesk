import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getToken } from '@/lib/api'

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/admin',
  beforeLoad: () => {
    if (!getToken()) {
      throw redirect({ to: '/login' })
    }

    throw redirect({ to: '/' })
  },
})
