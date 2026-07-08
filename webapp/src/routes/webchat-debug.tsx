import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'
import { AiDebugConsolePage } from '@/features/support-console/AiDebugConsolePage'

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat-debug',
  beforeLoad: () => { if (!getSupportToken()) throw redirect({ to: '/login' }) },
  component: AiDebugConsolePage,
})
